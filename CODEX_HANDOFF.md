# Codex Handoff — OurSteps Archive

This is the current public-safe handoff for code assistants. Read this file, `OPERATIONS.md`, and the relevant implementation/tests before changing production behavior.

## Stable production checkpoint

As of 2026-09-16, the four-stage archive close-out is complete and production validated.

Latest code checkpoint before this documentation update: `3d5828b` (`Respect shared queue lock ownership`).

Validated live state at close-out:

- full public archive: **5,522 articles**
- rolling recent-1y scope: **3,414 articles**
- public release healthcheck: **PASS**
- Guide Discovery V2 remains bounded/resumable; latest validated frontier is page **78**
- one historical item remains deliberately missing/review-required; do not force it complete
- existing incremental sync, historical backfill and Guide Discovery schedules remain loaded
- historical backfill is temporarily accelerated to **max 15 threads / 45 minutes per run** from 2026-09-19; cadence remains 07:00-21:00 every two hours and all throttle/retry/backoff protections remain unchanged
- FoxCloud on HTTPS 8443 remains outside this project and was not changed

These counts are a dated production snapshot, not constants. Normal scheduled sync/backfill can increase them.

As of 2026-09-17, Guide inventory is 6,179 and the public archive is 5,567; the gap is expected to shrink through scheduled historical backfill while Guide discovery may continue adding candidates.

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

## Owner homepage Control Center button

The full/owner homepage now receives a publisher-injected `Control Center` button linking to `/control-center.html`. The recent/tester homepage explicitly strips this link, so the UI follows the same physical scope separation as the Control Center assets themselves. Do not move this to shared preview HTML or client-side role detection.
## Temporary throughput tuning — 2026-09-19

The production historical-backfill LaunchAgent is now deliberately set to `--max-threads 15` after the 10-thread trial remained stable. The 45-minute cap, two-hour schedule, crawler throttle, retry/backoff, locks and publication chain are unchanged. Treat 15 as the current observation trial; do not raise it again or increase concurrency without reviewing real completion time, Busy/500/timeout rates and scheduler overlap first.
Private backfill throughput telemetry is recorded in `data/backfill-performance.json` plus append-only `data/logs/backfill-performance.jsonl`. It is counts/timings only and should be used to quantify requests-per-completed-thread plus crawl/preview-build/throttle/network/parse/persistence time before further tuning.
## Manual worker collision protection — 2026-09-18

Manual `Update`, `Authenticate`, Historical Backfill, and discovery `.command` launchers now call `scripts/archive_worker_status.py --guard` before doing work. The guard probes the NAS-native `data/worker.lock`; when a scheduled archive writer is active, manual work stops before authentication/network access. `OurSteps Worker Status.command` is the one-shot read-only status entry point. Preserve this guard when changing manual launchers.

## Live worker status + manual guard — 2026-09-18

Owner Control Center now has a near-real-time Archive Worker panel. `local.oursteps.worker-status` runs every 30 seconds and writes only a sanitized status snapshot; `/control-action/worker-status` is owner/full-scope only, and stale snapshots (>90s) become `UNKNOWN`. The web payload must never expose PID, command lines, paths, hostnames, credentials or session details. Manual archive/auth `.command` entry points also guard against an active NAS worker before starting. Keep this independent from the long-running action runner so RUNNING status continues to refresh during 20–45 minute jobs. The Control Center Historical Backfill action is aligned with the production scheduler at `--max-threads 15 --max-minutes 45`.
