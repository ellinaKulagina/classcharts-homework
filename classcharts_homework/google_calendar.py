"""Google Calendar adapter: in-memory service account auth and owned events only."""

import http.client
import json
import os
import re
import ssl
from dataclasses import dataclass
from urllib.parse import quote

from .client import ClassChartsError, ConfigurationError


class CalendarError(ClassChartsError):
    """Sanitized Calendar/auth failure. No request or response details attached."""


@dataclass(repr=False)
class _Reply:
    status: int
    headers: dict
    data: bytes


def _https(host, method, path, headers, body=None):
    """Only Google endpoints; no redirects, proxy environment, cookies or logs."""
    if host not in ("oauth2.googleapis.com", "www.googleapis.com"):
        raise CalendarError("Google request destination is not allowed.")
    connection = None
    try:
        connection = http.client.HTTPSConnection(host, timeout=20, context=ssl.create_default_context())
        connection.set_debuglevel(0)
        connection.request(method, path, body=body, headers=headers)
        reply = connection.getresponse()
        data = reply.read(5 * 1024 * 1024 + 1)
        if len(data) > 5 * 1024 * 1024:
            raise CalendarError("Google response exceeded the size limit.")
        return _Reply(reply.status, dict(reply.getheaders()), data)
    except (OSError, http.client.HTTPException, ValueError):
        raise CalendarError("Unable to contact Google securely. Try again later.") from None
    finally:
        if connection is not None:
            connection.close()


def _token_request(url, method="GET", body=None, headers=None, timeout=None, **kwargs):
    # google-auth's documented transport interface. Never trust a token URI from
    # arbitrary credential JSON, and never expose token endpoint error bodies.
    if url != "https://oauth2.googleapis.com/token" or method != "POST":
        raise CalendarError("Google authentication destination is not allowed.")
    reply = _https("oauth2.googleapis.com", method, "/token", headers or {}, body)
    if reply.status != 200:
        raise CalendarError("Google authentication failed. Check the service account key.")
    return reply


def _load_credentials(raw):
    try:
        from google.oauth2 import service_account
    except ImportError:
        raise ConfigurationError("Install requirements-calendar.txt before running calendar sync.") from None
    try:
        info = json.loads(raw)
        if not isinstance(info, dict) or info.get("type") != "service_account":
            raise ValueError
        if info.get("token_uri") != "https://oauth2.googleapis.com/token":
            raise ValueError
        if info.get("universe_domain", "googleapis.com") != "googleapis.com":
            raise ValueError
        # Allowlist fields: ignore credential-file directives, delegation,
        # custom endpoints, trust boundaries, or unrelated metadata.
        clean = {key: info[key] for key in ("client_email", "private_key", "token_uri")}
        return service_account.Credentials.from_service_account_info(
            clean, scopes=["https://www.googleapis.com/auth/calendar.events"]
        )
    except Exception:
        raise ConfigurationError("GOOGLE_SERVICE_ACCOUNT_JSON is not a valid Google service account key.") from None


class GoogleCalendar:
    """Only upsert events with this automation's ownership markers. No deletion."""

    def __init__(self, calendar_id, credentials):
        self._calendar_id = calendar_id
        self._credentials = credentials

    def __repr__(self):
        return "<GoogleCalendar redacted>"

    @classmethod
    def from_env(cls, environ=None):
        env = os.environ if environ is None else environ
        calendar_id = env.get("GOOGLE_CALENDAR_ID", "").strip()
        raw = env.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
        if not calendar_id or not raw:
            raise ConfigurationError("Set GOOGLE_CALENDAR_ID and GOOGLE_SERVICE_ACCOUNT_JSON.")
        if calendar_id == "primary" or len(calendar_id) > 1024 or any(ord(c) < 33 for c in calendar_id):
            raise ConfigurationError("Use the explicit ID of a dedicated Google calendar.")
        if len(raw) > 65536:
            raise ConfigurationError("GOOGLE_SERVICE_ACCOUNT_JSON exceeds the size limit.")
        return cls(calendar_id, _load_credentials(raw))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self._credentials = None
        self._calendar_id = None

    def check_access(self):
        """Validate the target even when there is no homework to synchronize."""
        self._json(self._request("GET", "?maxResults=1&fields=kind"))

    def _request(self, method, suffix, body=None, etag=None):
        if self._credentials is None:
            raise ConfigurationError("This calendar client is closed.")
        try:
            if not self._credentials.valid:
                self._credentials.refresh(_token_request)
            token = self._credentials.token
            if not isinstance(token, str) or not re.fullmatch(r"[\x21-\x7e]{1,8192}", token):
                raise ValueError
        except Exception:
            raise CalendarError("Google authentication failed. Check the service account key.") from None
        headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
        payload = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            payload = json.dumps(body, ensure_ascii=True).encode("ascii")
        if etag is not None:
            if not isinstance(etag, str) or not re.fullmatch(r"[\x20-\x7e]{1,1024}", etag):
                raise CalendarError("Google returned an invalid event version.")
            headers["If-Match"] = etag
        path = "/calendar/v3/calendars/" + quote(self._calendar_id, safe="") + "/events" + suffix
        return _https("www.googleapis.com", method, path, headers, payload)

    @staticmethod
    def _json(reply):
        if reply.status not in (200, 201):
            messages = {
                401: "Google rejected authentication. Check the service account key.",
                403: "Google denied access or quota. Check Calendar API access and calendar sharing.",
                404: "Google calendar or event was not found. Check the calendar ID and sharing.",
                409: "A calendar event conflict occurred. Run the sync again.",
                410: "A managed calendar event was deleted. Restore it in Google Calendar before syncing.",
                412: "A calendar event changed during sync. Run the sync again.",
                429: "Google rate limit reached. Try again later.",
            }
            raise CalendarError(messages.get(reply.status, "Google Calendar request failed. Try again later."))
        try:
            value = json.loads(reply.data)
            if not isinstance(value, dict):
                raise ValueError
            return value
        except (ValueError, UnicodeError, RecursionError):
            raise CalendarError("Google Calendar returned an invalid response.") from None

    @staticmethod
    def _owned(existing, desired):
        try:
            private = existing["extendedProperties"]["private"]
            expected = desired["extendedProperties"]["private"]
            owned = all(private.get(key) == value for key, value in expected.items())
        except (KeyError, TypeError, AttributeError):
            owned = False
        if not owned or existing.get("id") != desired["id"]:
            raise CalendarError("An event ID is already used by an unrelated event; no changes made to it.")
        if existing.get("status") == "cancelled":
            raise CalendarError("A managed calendar event was deleted. Restore it before syncing.")
        if existing.get("attendees") or existing.get("recurrence"):
            raise CalendarError("A managed event has guests or recurrence; remove these before syncing.")

    def upsert(self, desired, *, dry_run=True):
        event_id = desired["id"]
        if not re.fullmatch(r"[0-9a-v]{5,1024}", event_id):
            raise CalendarError("Invalid managed event identifier.")
        suffix = "/" + event_id
        reply = self._request("GET", suffix)
        if reply.status == 404:
            if dry_run:
                # A missing calendar can also return 404. Verify access to the
                # events collection so a dry run cannot falsely report success.
                self._json(self._request("GET", "?maxResults=1&fields=kind"))
                return
            created = self._request("POST", "?sendUpdates=none", desired)
            if created.status != 409:
                self._json(created)
                return
            # The prior run may have inserted successfully before a timeout, or
            # another process won the race. Re-read the SAME deterministic ID.
            reply = self._request("GET", suffix)
        existing = self._json(reply)
        self._owned(existing, desired)
        changes = {key: value for key, value in desired.items()
                   if key not in ("id", "extendedProperties") and existing.get(key) != value}
        if not changes or dry_run:
            return
        etag = existing.get("etag")
        if not etag:
            raise CalendarError("Google returned no event version; refusing an unsafe update.")
        self._json(self._request("PUT", suffix + "?sendUpdates=none", desired, etag=etag))
