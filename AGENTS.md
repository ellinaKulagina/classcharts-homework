# Repository rules

- Never commit real pupil codes, dates of birth, names, homework, tokens, cookies,
  API responses, screenshots, or other personal data. This includes test fixtures.
- Use environment variables for credentials and entirely synthetic test data.
- Keep authentication, homework and session data in memory. Do not log request
  bodies, headers, response bodies, or raw transport/server exceptions.
- Keep the client read-only with respect to homework; do not mark work complete.
- Tests must run offline: `python3 -m unittest discover -s tests -v`.
- Google Calendar and GitHub Actions integration is authorized. Only create or
  update this automation's events; never delete events or modify unrelated ones.
- Calendar sync must be idempotent, dry-run capable, and silent about personal
  data. Workflows must use repository secrets, minimum permissions, and no data
  artifacts. Run tests without credentials or network access.
- Inspect staged changes for personal data before any commit. Ignore rules are
  safeguards, not a guarantee: do not force-add private files.
