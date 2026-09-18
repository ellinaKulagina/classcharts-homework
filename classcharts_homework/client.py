"""Fixed-origin HTTPS client. All public errors are safe to print without locals."""

import http.client
import json
import os
import re
import ssl
from dataclasses import dataclass, field
from datetime import date
from http.cookies import CookieError, SimpleCookie
from typing import Mapping, Optional
from urllib.parse import unquote, urlencode


class ClassChartsError(Exception):
    """Base class for sanitized client failures."""


class ConfigurationError(ClassChartsError):
    pass


class AuthenticationError(ClassChartsError):
    pass


class APIError(ClassChartsError):
    pass


class NetworkError(ClassChartsError):
    pass


def _iso_date(value: str, label: str) -> date:
    try:
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError
        return date.fromisoformat(value)
    except ValueError:
        raise ConfigurationError(label + " must be a valid YYYY-MM-DD date.") from None


@dataclass(frozen=True, repr=False)
class _Credentials:
    code: str
    dob: date

    def __repr__(self) -> str:
        return "<Credentials redacted>"


@dataclass(frozen=True)
class Homework:
    """Minimal homework fields for a future calendar adapter; values are private.

    Date strings retain the server's original timezone/format. Descriptions may
    contain HTML and must be treated as untrusted text by downstream consumers.
    """

    id: int = field(repr=False)
    title: str = field(repr=False)
    description: Optional[str] = field(repr=False)
    subject: Optional[str] = field(repr=False)
    due_date: Optional[str] = field(repr=False)
    issue_date: Optional[str] = field(repr=False)
    status: Optional[str] = field(repr=False)
    ticked: Optional[bool] = field(repr=False)


@dataclass(frozen=True, repr=False)
class _Response:
    status: int
    headers: list
    body: bytes


class _HTTPS:
    """No redirects, cookie jar, proxy environment, disk cache, or HTTP logging."""

    MAX_BYTES = 5 * 1024 * 1024

    def request(self, method: str, path: str, headers: dict, body=None) -> _Response:
        connection = None
        try:
            connection = http.client.HTTPSConnection(
                "www.classcharts.com", timeout=20, context=ssl.create_default_context()
            )
            connection.set_debuglevel(0)
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            payload = response.read(self.MAX_BYTES + 1)
            if len(payload) > self.MAX_BYTES:
                raise APIError("ClassCharts response exceeded the size limit.")
            return _Response(response.status, response.getheaders(), payload)
        except (OSError, http.client.HTTPException, ValueError):
            raise NetworkError("Unable to contact ClassCharts securely. Try again later.") from None
        finally:
            if connection is not None:
                connection.close()


def _token(value) -> str:
    # Tokens become headers: reject whitespace/control characters and huge values.
    if not isinstance(value, str) or not re.fullmatch(r"[\x21-\x7e]{1,4096}", value):
        raise AuthenticationError("ClassCharts returned an invalid session.")
    return value


def _student_id(value) -> int:
    if type(value) is not int or value <= 0:
        raise APIError("ClassCharts returned an invalid student identifier.")
    return value


def _homework(item) -> Homework:
    if not isinstance(item, dict) or type(item.get("id")) is not int or item["id"] <= 0:
        raise APIError("ClassCharts returned an invalid homework item.")
    if not isinstance(item.get("title"), str):
        raise APIError("ClassCharts returned an invalid homework title.")
    fields = ("description", "subject", "due_date", "issue_date")
    if any(item.get(key) is not None and not isinstance(item[key], str) for key in fields):
        raise APIError("ClassCharts returned invalid homework fields.")
    status = item.get("status")
    if status is None:
        status = {}
    if not isinstance(status, dict):
        raise APIError("ClassCharts returned an invalid homework status.")
    state = status.get("state")
    if state is not None and not isinstance(state, str):
        raise APIError("ClassCharts returned an invalid homework status.")
    ticked = status.get("ticked")
    if ticked is not None and ticked not in ("yes", "no"):
        raise APIError("ClassCharts returned an invalid homework completion flag.")
    return Homework(
        id=item["id"], title=item["title"],
        description=item.get("description"), subject=item.get("subject"),
        due_date=item.get("due_date"), issue_date=item.get("issue_date"),
        status=state, ticked=None if ticked is None else ticked == "yes",
    )


class StudentClient:
    """Use from_env() and a context manager. Instances are not thread-safe."""

    def __init__(self, credentials: _Credentials):
        self._credentials = credentials
        self._http = _HTTPS()
        self._session = None
        self._cookies = ""
        self._pupil_id = None

    def __repr__(self) -> str:
        return "<StudentClient redacted>"

    @property
    def student_id(self) -> int:
        """Authenticated identity for stable sync IDs; never print or persist it."""
        if self._pupil_id is None:
            raise ConfigurationError("Authenticate before requesting the student identifier.")
        return self._pupil_id

    @classmethod
    def from_env(cls, environ: Optional[Mapping[str, str]] = None) -> "StudentClient":
        env = os.environ if environ is None else environ
        code = env.get("CLASSCHARTS_PUPIL_CODE", "").strip()
        dob = env.get("CLASSCHARTS_DOB", "").strip()
        if not code or not dob:
            raise ConfigurationError("Set CLASSCHARTS_PUPIL_CODE and CLASSCHARTS_DOB.")
        if len(code) > 256 or not code.isascii() or not code.isalnum():
            raise ConfigurationError("CLASSCHARTS_PUPIL_CODE must contain only letters and digits.")
        parsed_dob = _iso_date(dob, "CLASSCHARTS_DOB")
        if parsed_dob > date.today():
            raise ConfigurationError("CLASSCHARTS_DOB must not be in the future.")
        return cls(_Credentials(code.upper(), parsed_dob))

    def __enter__(self) -> "StudentClient":
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def _clear_session(self) -> None:
        self._session = None
        self._cookies = ""
        self._pupil_id = None

    def close(self) -> None:
        """Drop private references; Python cannot guarantee memory zeroization."""
        self._clear_session()
        self._credentials = None

    def _request(self, method: str, path: str, form=None) -> _Response:
        headers = {"Accept": "application/json", "User-Agent": "classcharts-homework/0.1"}
        body = None
        if form is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            body = urlencode(form).encode("ascii")
        if self._session:
            headers["Authorization"] = "Basic " + self._session
            headers["Cookie"] = self._cookies
        response = self._http.request(method, path, headers, body)
        if response.status in (401, 403):
            self._clear_session()
            raise AuthenticationError("ClassCharts denied access; verify credentials or sign in manually.")
        if response.status == 429:
            raise APIError("ClassCharts rate limit reached. Try again later.")
        if response.status >= 500:
            raise APIError("ClassCharts is temporarily unavailable. Try again later.")
        return response

    def _json(self, response: _Response, *, authenticating=False) -> dict:
        if response.status != 200:
            raise APIError("Unexpected ClassCharts HTTP response; redirects are not followed.")
        try:
            payload = json.loads(response.body)
        except (ValueError, UnicodeError, RecursionError):
            raise APIError("ClassCharts returned an invalid JSON response.") from None
        if not isinstance(payload, dict) or type(payload.get("success")) is not int:
            raise APIError("ClassCharts returned an invalid response envelope.")
        if payload["success"] != 1:
            if authenticating:
                self._clear_session()
                raise AuthenticationError("ClassCharts could not validate the student session.")
            raise APIError("ClassCharts rejected the homework request.")
        meta = payload.get("meta")
        if isinstance(meta, dict) and "session_id" in meta:
            self._session = _token(meta["session_id"])
        return payload

    def login(self) -> None:
        """Authenticate through the student form, then validate via API ping."""
        self._clear_session()
        if self._credentials is None:
            raise ConfigurationError("This client is closed; create a new client from the environment.")
        try:
            response = self._request("POST", "/student/login", {
                "_method": "POST", "code": self._credentials.code,
                "dob": self._credentials.dob.strftime("%d/%m/%Y"),
                "remember_me": "0", "recaptcha-token": "no-token-available",
            })
            # A successful website login returns cookies on a redirect. We never
            # visit the redirect target, so credentials cannot cross origins.
            if response.status != 302:
                raise AuthenticationError(
                    "ClassCharts login failed; verify credentials or complete login on the website."
                )
            cookies = SimpleCookie()
            for name, value in response.headers:
                if name.lower() == "set-cookie":
                    cookies.load(value)
            session_cookie = cookies.get("student_session_credentials")
            if session_cookie is None:
                raise AuthenticationError("ClassCharts did not provide a student session cookie.")
            session = json.loads(unquote(session_cookie.value))
            if not isinstance(session, dict):
                raise ValueError
            self._session = _token(session.get("session_id"))
            self._cookies = "; ".join(
                name + "=" + morsel.coded_value for name, morsel in cookies.items()
            )
            self._ping()
        except (CookieError, ValueError, UnicodeError, RecursionError):
            self._clear_session()
            raise AuthenticationError("ClassCharts returned invalid authentication cookies.") from None
        except ClassChartsError:
            self._clear_session()
            raise

    def _ping(self) -> None:
        payload = self._json(self._request(
            "POST", "/apiv2student/ping", {"include_data": "true"}
        ), authenticating=True)
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("user"), dict):
            raise APIError("ClassCharts returned an invalid student response.")
        self._pupil_id = _student_id(data["user"].get("id"))

    def get_homework(
        self, *, from_date: str, to_date: str, display_date: str = "due_date"
    ) -> list[Homework]:
        """Fetch a bounded date range. Each call validates/refreshes the session.

        Returns only the requested range, not all historical homework. No retries
        are automatic, including authentication failures and rate limits.
        """
        start = _iso_date(from_date, "from_date")
        end = _iso_date(to_date, "to_date")
        if start > end:
            raise ConfigurationError("from_date must be on or before to_date.")
        if display_date not in ("due_date", "issue_date"):
            raise ConfigurationError("display_date must be due_date or issue_date.")
        if self._session is None:
            self.login()
        else:
            self._ping()
        query = urlencode({"from": start.isoformat(), "to": end.isoformat(),
                           "display_date": display_date})
        payload = self._json(self._request(
            "GET", "/apiv2student/homeworks/" + str(self._pupil_id) + "?" + query
        ))
        if not isinstance(payload.get("data"), list):
            raise APIError("ClassCharts returned an invalid homework list.")
        return [_homework(item) for item in payload["data"]]
