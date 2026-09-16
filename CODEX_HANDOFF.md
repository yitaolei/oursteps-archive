# Codex Handoff — OurSteps Archive

This is the current public-safe handoff for code assistants. Read this file, `OPERATIONS.md`, and the relevant implementation/tests before changing production behavior.

## Stable production checkpoint

As of 2026-09-16, the four-stage archive close-out is complete and production validated.

Latest code checkpoint before this documentation update: `3d5828b` (`Respect shared queue lock ownership`).

Validated live state at close-out:

- full public archive: **5,522 articles**
- rolling recent-1y scope: **3,414 articles**
- public release healthcheck: **PASS**
- Guide Discovery V2 remains bounded/resumable; latest validated frontier is page **55**
- one historical item remains deliberately missing/review-required; do not force it complete
- existing incremental sync, historical backfill and Guide Discovery schedules remain loaded
- FoxCloud on HTTPS 8443 remains outside this project and was not changed

These counts are a dated production snapshot, not constants. Normal scheduled sync/backfill can increase them.

## Completed close-out phases

### Phase 1 — Documentation & Tool Registry

Complete. `OPERATIONS.md` and `config/tool_registry.json` define supported capabilities and safety boundaries. The registry is metadata only and does not authorize command execution.

### Phase 2 — 8448 Control Center

Complete and production validated. The owner/full scope receives generated Control Center assets; the recent/tester scope physically does not contain them. Operational status is a sanitized static snapshot. Do not expose raw SQLite, logs, credentials, cookies, sessions or arbitrary filesystem data.

### Phase 3 — First-party article reads

Complete and production validated. A dedicated nginx log records only:

- timestamp
- article URI/TID
- HTTP status

It deliberately omits IP, Basic Auth username, User-Agent, Cookie and session data. Successful article GETs are aggregated into generated `article-views.json`. Article pages label this metric `本站阅读`; the existing `浏览` value remains the source OurSteps forum view count.

Real production validation recorded two successful reads for a test article and published the aggregate successfully.

### Phase 4 — Safe Action Buttons

Complete and production validated end-to-end.

Allowed web actions are exactly:

- `incremental_sync`
- `guide_discovery_v2`
- `historical_backfill`
- `publish_public`

`rollback_public` remains Terminal-only. `authenticate` remains local-only.

Execution path:

1. owner-authenticated Control Center submits a fixed action ID
2. nginx passes the authenticated scope to an internal queue-only API
3. the API writes only to the private action queue and has no published host port, SSH, Keychain, Docker, SQLite or secret access
4. the Mac-side `local.oursteps.control-actions` runner polls every 60 seconds
5. the runner uses its own hard-coded allowlist and existing supported launchers
6. sanitized job state returns to the Control Center

Production validation observed a real `publish_public` job move through pending/running/succeeded, then complete the public healthcheck successfully.

## Important Phase 4 production fixes

Two bugs were found only during live browser validation and are now fixed. Do not reintroduce them.

1. **Basic Auth timing:** do not gate `/control-action/` with a rewrite-phase `$remote_user`/`$archive_scope` `if`. Basic Auth must complete first; the internal API independently enforces `X-Oursteps-Scope == full`.
2. **Shared queue ownership:** the capability-dropped action API must not chmod an existing shared lock file that it does not own. Shared queue lock/job files are writable by both the internal API and Mac runner; new queue JSON remains sanitized and contains no secrets.

Relevant checkpoints:

- `8447eae` — initial safe Control Center actions
- `6e3ce3b` — fix Control Center action auth gate
- `40b2de7` — fix shared queue permissions
- `3d5828b` — respect shared queue lock ownership

## Do not redo completed work

Unless a demonstrated regression requires it, do not redesign or repeat:

- global search / global sorting UX (`87a592c`)
- Guide Discovery V2
- historical scheduler/backfill auto-publish
- owner/recent role-scoped publishing
- Phase 1–4 close-out architecture

Preserve crawler throttle/backoff, robots handling, authentication verification, worker/publish locks, `review_required` semantics and NAS-native SQLite operation.

## Token-saving operating rule

Routine status, logs, launchd checks, Git inspection, existing tests, healthchecks and read-only database inspection should be done with Terminal/Desktop Commander. Use Codex only for a necessary code change, complex cross-file diagnosis, refactor or genuinely new test. Do not spend Codex tokens waiting on long-running commands or repeating already-passed work.

## Before the next code change

1. Check `git status` and current HEAD.
2. Read this file and `OPERATIONS.md`.
3. Inspect only the implementation/tests relevant to the requested change.
4. Preserve production boundaries listed above.
5. Run focused tests first; avoid unrelated historical benchmarks.
6. After important changes: update handoff docs, commit and push `main`.
