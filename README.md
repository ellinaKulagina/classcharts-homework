# ClassCharts homework

A read-only Python student client for the first stage of homework automation.
Google Calendar and GitHub Actions integration are not implemented yet.

The client authenticates using environment variables, retrieves homework for an
explicit date range, and keeps credentials, cookies, tokens and results in memory.
It has no third-party runtime dependencies and runs on Python 3.9 or newer.

## Run the offline tests

From this repository directory:

```sh
python3 -m unittest discover -s tests -v
```

All fixtures are invented. Tests block real network connections and cover the
login/ping/homework contract, session refresh, malformed responses, expired
sessions, rate limits, verified TLS, response limits, and output redaction.

## Configure credentials privately

Set these process environment variables:

| Variable | Value |
| --- | --- |
| `CLASSCHARTS_PUPIL_CODE` | School-issued student/pupil code |
| `CLASSCHARTS_DOB` | Date of birth in `YYYY-MM-DD` format |

`.env.example` contains blank variable names only. The application deliberately
does not load `.env` files. Use your local secret manager or enter values at hidden
terminal prompts. The following works in Bash and Zsh; enter secrets only when
prompted, never as commands or command-line arguments:

```sh
printf 'Pupil code: '
read -r -s CLASSCHARTS_PUPIL_CODE
printf '\nDOB (YYYY-MM-DD): '
read -r -s CLASSCHARTS_DOB
printf '\n'
export CLASSCHARTS_PUPIL_CODE CLASSCHARTS_DOB

python3 -m classcharts_homework --from 2026-09-18 --to 2026-10-18

unset CLASSCHARTS_PUPIL_CODE CLASSCHARTS_DOB
```

Choose the query dates you need. Success prints a fixed confirmation only, with
no homework titles, counts, dates, pupil identifiers, or tokens. An empty homework
list is also a successful retrieval. Failure prints a sanitized error to stderr
and returns a nonzero exit status. No output file is created.

The default filter is `due_date`; pass `--display-date issue_date` to filter by
issue date instead. Missing, invalid, or reversed dates fail before login.

## Use as a library

```python
from classcharts_homework import StudentClient

with StudentClient.from_env() as client:
    homework = client.get_homework(
        from_date="2026-09-18",
        to_date="2026-10-18",
        display_date="due_date",
    )
    # Consume in memory. Do not print, log, serialize or commit these values.
```

Each `Homework` has `id`, `title`, `description`, `subject`, `due_date`,
`issue_date`, `status`, and `ticked`. Missing optional fields remain `None`;
unknown completion is not silently treated as incomplete. Date strings preserve
the server's format and timezone. Descriptions may contain untrusted HTML.
Student names, teacher details, marks and attachment links are discarded.
The results are only the requested date range, not all historical homework.

The context manager drops the client's credential and session references when
it exits. Returned homework remains the caller's responsibility. Python cannot
guarantee zeroization of memory, and the client does not remove environment
variables from its parent shell. Client instances are not thread-safe.

## Authentication and request behavior

The implementation follows the current community client's authentication flow:

1. POST the student login form to `https://www.classcharts.com/student/login`,
   converting the environment DOB to `DD/MM/YYYY`. Request no remembered login.
2. Read the `student_session_credentials` cookie from the 302 response without
   following the redirect. Keep cookies and the session token in memory.
3. POST `/apiv2student/ping` with `include_data=true` and `Authorization: Basic
   <session_id>`, then use the validated student ID and any rotated session token.
4. GET `/apiv2student/homeworks/{student_id}` with the explicit date range.

Every subsequent retrieval pings first to refresh the session. Requests use a
fixed HTTPS origin, certificate and hostname verification, a 20-second socket
timeout, and a 5 MiB response limit. Proxy environment variables are not used.
There are no automatic retries; authentication failures and rate limits must not
cause repeated logins. HTTP 401/403 clears the session. Errors never include
server response bodies, cookies, credentials, or raw transport exception text.

This is an **unofficial API**, and school-specific authentication or CAPTCHA
requirements may prevent unattended login. The client uses the community
client's `no-token-available` form value; it does not solve challenges. A denied
login fails explicitly. No live account has been used to verify this initial
implementation; the passing tests establish the mocked protocol and privacy
behavior, not acceptance by ClassCharts for a particular pupil.

Protocol references checked on 2026-09-18:

- [ClassCharts student login](https://www.classcharts.com/student/login)
- [Community student client source, pinned revision](https://github.com/classchartsapi/classcharts-api-js/blob/15c9b55df7912cac1bfb21fb3d9fe345145b5322/src/core/studentClient.ts)
- [Community API request and homework source, pinned revision](https://github.com/classchartsapi/classcharts-api-js/blob/15c9b55df7912cac1bfb21fb3d9fe345145b5322/src/core/baseClient.ts)

## Privacy rules

Never commit pupil codes, real DOBs, names, homework, credentials, tokens, cookies,
API dumps, logs, screenshots, or other personal data, including in fixtures or
issue reports. Do not enable HTTP debug logging or exception reporters that
capture local variables. Default object representations omit private values,
but accessing fields or explicitly serializing objects can expose them.

`.gitignore` excludes common secret files, response exports, logs and local data
directories. Ignore patterns cannot detect every secret and can be overridden:
inspect staged changes before every commit and never force-add private files.
Future Calendar and Actions work must preserve these rules, including avoiding
personal data in workflow logs, caches, and uploaded artifacts.
