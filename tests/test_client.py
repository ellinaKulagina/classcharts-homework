"""All values are fabricated. No live credentials or recorded API responses."""

import io
import json
import socket
import ssl
import traceback
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch
from urllib.parse import parse_qs, quote, urlsplit

from classcharts_homework import (
    APIError, AuthenticationError, ConfigurationError, NetworkError, StudentClient,
)
from classcharts_homework.__main__ import main
from classcharts_homework.client import _HTTPS, _Response


ENV = {"CLASSCHARTS_PUPIL_CODE": "TESTCODEONLY", "CLASSCHARTS_DOB": "2000-01-02"}
SESSION = "TESTSESSIONONLY"
ROTATED = "TESTROTATEDONLY"
PRIVATE = "SYNTHETIC_PRIVATE_SENTINEL"


def response(payload, status=200):
    return _Response(status, [], json.dumps(payload).encode())


def login_response():
    cookie = quote(json.dumps({"session_id": SESSION}))
    return _Response(302, [
        ("Set-Cookie", "student_session_credentials=" + cookie + "; Secure; HttpOnly; Path=/"),
        ("Set-Cookie", "TESTCOOKIE=fake; Secure; HttpOnly; Path=/"),
        ("Location", "/student"),
    ], b"")


def ping_response(token=ROTATED):
    return response({"success": 1, "data": {"user": {"id": 123, "name": PRIVATE}},
                     "meta": {"session_id": token}})


def homework_item():
    return {"id": 456, "title": PRIVATE, "description": "<p>Fabricated exercise</p>",
            "subject": "Synthetic subject", "due_date": "2030-01-10T00:00:00+00:00",
            "issue_date": "2030-01-01", "teacher": PRIVATE,
            "status": {"state": "not_completed", "ticked": "no", "mark": PRIVATE},
            "validated_attachments": [{"file": PRIVATE}]}


class OfflineTest(unittest.TestCase):
    def setUp(self):
        # Fail closed even if a future test accidentally forgets to mock transport.
        self.network = patch("socket.create_connection", side_effect=AssertionError("Live network forbidden"))
        self.network.start()
        self.addCleanup(self.network.stop)


class ClientTests(OfflineTest):
    def setUp(self):
        super().setUp()
        self.client = StudentClient.from_env(ENV)
        self.addCleanup(self.client.close)
        self.http = patch.object(self.client._http, "request")
        self.request = self.http.start()
        self.addCleanup(self.http.stop)

    def prepare(self, final=None):
        if final is None:
            final = response({"success": 1, "data": [homework_item()]})
        self.request.side_effect = [login_response(), ping_response(), final]

    def fetch(self, **kwargs):
        options = {"from_date": "2030-01-01", "to_date": "2030-01-31"}
        options.update(kwargs)
        return self.client.get_homework(**options)

    def test_login_ping_and_homework_contract(self):
        self.prepare()
        items = self.fetch()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].id, 456)
        self.assertEqual(items[0].title, PRIVATE)
        self.assertFalse(items[0].ticked)
        self.assertEqual(items[0].due_date, "2030-01-10T00:00:00+00:00")
        self.assertFalse(hasattr(items[0], "teacher"))
        self.assertFalse(hasattr(items[0], "validated_attachments"))
        login, ping, homework = [call.args for call in self.request.call_args_list]
        self.assertEqual(login[:2], ("POST", "/student/login"))
        self.assertNotIn("Authorization", login[2])
        self.assertNotIn("Cookie", login[2])
        self.assertEqual(parse_qs(login[3].decode()), {
            "_method": ["POST"], "code": [ENV["CLASSCHARTS_PUPIL_CODE"]],
            "dob": ["02/01/2000"], "remember_me": ["0"],
            "recaptcha-token": ["no-token-available"],
        })
        self.assertEqual(ping[:2], ("POST", "/apiv2student/ping"))
        self.assertEqual(ping[2]["Authorization"], "Basic " + SESSION)
        self.assertEqual(parse_qs(ping[3].decode()), {"include_data": ["true"]})
        self.assertEqual(homework[0], "GET")
        self.assertEqual(urlsplit(homework[1]).path, "/apiv2student/homeworks/123")
        self.assertEqual(parse_qs(urlsplit(homework[1]).query), {
            "from": ["2030-01-01"], "to": ["2030-01-31"], "display_date": ["due_date"],
        })
        self.assertEqual(homework[2]["Authorization"], "Basic " + ROTATED)
        self.assertIn("TESTCOOKIE=fake", homework[2]["Cookie"])
        self.assertNotIn("HttpOnly", homework[2]["Cookie"])
        self.assertIsNone(homework[3])

    def test_repeated_fetch_refreshes_session_without_reposting_credentials(self):
        self.request.side_effect = [login_response(), ping_response(),
                                    response({"success": 1, "data": []}),
                                    ping_response("TESTNEXTONLY"),
                                    response({"success": 1, "data": []})]
        self.assertEqual(self.fetch(), [])
        self.assertEqual(self.fetch(display_date="issue_date"), [])
        calls = self.request.call_args_list
        self.assertEqual(len(calls), 5)
        self.assertEqual(calls[3].args[1], "/apiv2student/ping")
        self.assertEqual(calls[4].args[2]["Authorization"], "Basic TESTNEXTONLY")
        self.assertIn("display_date=issue_date", calls[4].args[1])

    def test_repr_hides_credentials_sessions_and_homework(self):
        self.prepare()
        items = self.fetch()
        rendered = repr((self.client, self.client._credentials, items))
        for value in (*ENV.values(), SESSION, ROTATED, PRIVATE, "456", "2030"):
            self.assertNotIn(value, rendered)

    def test_empty_homework_is_success(self):
        self.prepare(response({"success": 1, "data": []}))
        self.assertEqual(self.fetch(), [])

    def test_nullable_fields_and_unknown_completion(self):
        self.prepare(response({"success": 1, "data": [{"id": 1, "title": "Synthetic"}]}))
        item = self.fetch()[0]
        self.assertIsNone(item.due_date)
        self.assertIsNone(item.ticked)

    def test_completion_flag(self):
        item = homework_item()
        item["status"]["ticked"] = "yes"
        self.prepare(response({"success": 1, "data": [item]}))
        self.assertTrue(self.fetch()[0].ticked)

    def test_invalid_query_fails_before_network(self):
        for options in ({"from_date": "PRIVATE"}, {"to_date": "2030-02-30"},
                        {"from_date": "2030-02-01"}, {"display_date": "PRIVATE"},
                        {"from_date": "20300101"}):
            with self.subTest(options=options), self.assertRaises(ConfigurationError):
                self.fetch(**options)
        self.request.assert_not_called()

    def test_failed_login_never_echoes_response(self):
        self.request.return_value = _Response(200, [], PRIVATE.encode())
        with self.assertRaises(AuthenticationError) as caught:
            self.fetch()
        self.assertNotIn(PRIVATE, str(caught.exception))
        self.assertEqual(self.request.call_count, 1)
        self.assertIsNone(self.client._session)

    def test_redirect_without_session_is_rejected(self):
        self.request.return_value = _Response(302, [("Location", "https://example.invalid")], b"")
        with self.assertRaises(AuthenticationError):
            self.fetch()
        self.assertEqual(self.request.call_count, 1)

    def test_invalid_cookie_is_sanitized(self):
        for cookie in (PRIVATE, quote("[]"), quote('{"session_id":"bad\\r\\nheader"}'),
                       quote('{"session_id":null}')):
            with self.subTest(cookie=cookie):
                self.request.return_value = _Response(302, [
                    ("Set-Cookie", "student_session_credentials=" + cookie)], b"")
                with self.assertRaises(AuthenticationError) as caught:
                    self.fetch()
                self.assertNotIn(PRIVATE, str(caught.exception))
                self.assertIsNone(self.client._session)

    def test_expired_session_is_cleared_without_retry(self):
        self.prepare(response({"error": PRIVATE}, 401))
        with self.assertRaises(AuthenticationError):
            self.fetch()
        self.assertEqual(self.request.call_count, 3)
        self.assertIsNone(self.client._session)
        self.assertEqual(self.client._cookies, "")

    def test_ping_failure_clears_session(self):
        self.request.side_effect = [login_response(), response({"success": 0, "error": PRIVATE})]
        with self.assertRaises(AuthenticationError) as caught:
            self.fetch()
        self.assertNotIn(PRIVATE, str(caught.exception))
        self.assertIsNone(self.client._session)
        self.assertEqual(self.request.call_count, 2)

    def test_invalid_student_id_is_never_inserted_in_request_path(self):
        for value in (True, 0, -1, "../private", None):
            self.request.side_effect = [login_response(), response({
                "success": 1, "data": {"user": {"id": value}}})]
            with self.subTest(value=value), self.assertRaises(APIError):
                self.fetch()

    def test_http_errors_are_sanitized_and_not_retried(self):
        for status, error in ((403, AuthenticationError), (429, APIError),
                              (503, APIError), (302, APIError), (404, APIError)):
            with self.subTest(status=status):
                self.client._clear_session()
                self.request.reset_mock()
                self.prepare(_Response(status, [], PRIVATE.encode()))
                with self.assertRaises(error) as caught:
                    self.fetch()
                self.assertNotIn(PRIVATE, str(caught.exception))
                self.assertEqual(self.request.call_count, 3)

    def test_application_failure_is_not_an_empty_list(self):
        self.prepare(response({"success": 0, "error": PRIVATE, "data": []}))
        with self.assertRaises(APIError) as caught:
            self.fetch()
        self.assertNotIn(PRIVATE, str(caught.exception))

    def test_malformed_responses_are_sanitized(self):
        for payload in (b"<html>" + PRIVATE.encode(), b"\xff", b"[]",
                        b'{"success":true,"data":[]}', b'{"data":[]}',
                        b'{"success":1,"data":{}}'):
            with self.subTest(payload=payload):
                self.client._clear_session()
                self.prepare(_Response(200, [], payload))
                with self.assertRaises(APIError) as caught:
                    self.fetch()
                self.assertNotIn(PRIVATE, str(caught.exception))

    def test_malformed_homework_is_not_silently_skipped(self):
        for changes in ({"id": True}, {"title": None}, {"description": []},
                        {"status": []}, {"status": {"state": {}}},
                        {"status": {"ticked": "unexpected"}}):
            with self.subTest(changes=changes):
                self.client._clear_session()
                self.prepare(response({"success": 1, "data": [{**homework_item(), **changes}]}))
                with self.assertRaises(APIError):
                    self.fetch()

    def test_context_manager_drops_private_references_on_error(self):
        with self.assertRaises(RuntimeError):
            with self.client:
                self.client._session = SESSION
                raise RuntimeError("Synthetic failure")
        self.assertIsNone(self.client._credentials)
        self.assertIsNone(self.client._session)
        self.assertEqual(self.client._cookies, "")
        with self.assertRaises(ConfigurationError):
            self.fetch()


class EnvironmentTests(OfflineTest):
    def test_reads_real_process_environment_without_loading_dotenv(self):
        with patch.dict("os.environ", ENV, clear=True):
            with StudentClient.from_env() as client:
                self.assertEqual(client._credentials.code, ENV["CLASSCHARTS_PUPIL_CODE"])

    def test_missing_or_empty_environment_fails(self):
        for env in ({}, {"CLASSCHARTS_PUPIL_CODE": "TEST"},
                    {**ENV, "CLASSCHARTS_DOB": " "}):
            with self.subTest(env=env), self.assertRaises(ConfigurationError):
                StudentClient.from_env(env)

    def test_invalid_credentials_are_not_echoed(self):
        for env in ({**ENV, "CLASSCHARTS_DOB": PRIVATE},
                    {**ENV, "CLASSCHARTS_DOB": "2000-02-30"},
                    {**ENV, "CLASSCHARTS_DOB": "9999-01-01"},
                    {**ENV, "CLASSCHARTS_PUPIL_CODE": PRIVATE + "\n"}):
            with self.subTest(env=env), self.assertRaises(ConfigurationError) as caught:
                StudentClient.from_env(env)
            self.assertNotIn(PRIVATE, str(caught.exception))

    def test_whitespace_and_code_case_normalized(self):
        with StudentClient.from_env({**ENV, "CLASSCHARTS_PUPIL_CODE": " testcodeonly "}) as client:
            self.assertEqual(client._credentials.code, "TESTCODEONLY")


class TransportTests(OfflineTest):
    def setUp(self):
        super().setUp()
        self.factory_patch = patch("http.client.HTTPSConnection")
        self.factory = self.factory_patch.start()
        self.addCleanup(self.factory_patch.stop)
        self.connection = self.factory.return_value
        self.response = self.connection.getresponse.return_value
        self.response.status = 200
        self.response.getheaders.return_value = []
        self.response.read.return_value = b"{}"

    def test_fixed_host_verified_tls_timeout_and_no_debug_output(self):
        _HTTPS().request("GET", "/test", {})
        args, kwargs = self.factory.call_args
        self.assertEqual(args, ("www.classcharts.com",))
        self.assertEqual(kwargs["timeout"], 20)
        self.assertTrue(kwargs["context"].check_hostname)
        self.assertEqual(kwargs["context"].verify_mode, ssl.CERT_REQUIRED)
        self.connection.set_debuglevel.assert_called_once_with(0)
        self.connection.close.assert_called_once()

    def test_transport_does_not_follow_redirects(self):
        self.response.status = 302
        self.response.getheaders.return_value = [("Location", "https://example.invalid")]
        self.assertEqual(_HTTPS().request("POST", "/student/login", {}).status, 302)
        self.connection.request.assert_called_once()

    def test_network_errors_do_not_leak_into_standard_tracebacks(self):
        for error in (socket.timeout(PRIVATE), ssl.SSLError(PRIVATE),
                      OSError(PRIVATE), ValueError(PRIVATE)):
            self.connection.request.side_effect = error
            try:
                _HTTPS().request("POST", "/student/login", {}, PRIVATE.encode())
            except NetworkError:
                self.assertNotIn(PRIVATE, traceback.format_exc())
            else:
                self.fail("Expected sanitized network error")
        self.assertEqual(self.connection.close.call_count, 4)

    def test_response_size_is_bounded(self):
        with patch.object(_HTTPS, "MAX_BYTES", 10):
            self.response.read.return_value = b"x" * 11
            with self.assertRaises(APIError):
                _HTTPS().request("GET", "/test", {})
            self.response.read.assert_called_once_with(11)
            self.connection.close.assert_called_once()


class CLITests(OfflineTest):
    def test_success_prints_no_personal_data_or_counts(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.dict("os.environ", ENV, clear=True), patch.object(_HTTPS, "request", side_effect=[
            login_response(), ping_response(), response({"success": 1, "data": [homework_item()]})
        ]), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["--from", "2030-01-01", "--to", "2030-01-31"])
        self.assertEqual(code, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(stdout.getvalue(),
                         "Homework retrieval succeeded. No personal data was written or displayed.\n")

    def test_failure_is_sanitized_and_nonzero(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.dict("os.environ", ENV, clear=True), patch.object(_HTTPS, "request", return_value=
            _Response(200, [], PRIVATE.encode())
        ), redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["--from", "2030-01-01", "--to", "2030-01-31"])
        self.assertEqual(code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertNotIn(PRIVATE, stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
