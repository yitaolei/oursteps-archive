# OurSteps Archive Operations

This is the operator-facing map for the production OurSteps Archive system. It records supported entry points, schedules, safety boundaries, and the staged control-center plan without exposing private deployment credentials.

## Production boundaries

- Public archive: HTTPS port `8448`.
- Existing role scopes must remain intact, including the configured owner/full-archive and tester/recent-access accounts.
- FoxCloud on HTTPS port `8443` is outside this project and must not be changed.
- Private archive data remains on the NAS. Do not expose SQLite, raw HTML, logs, cookies, sessions, Keychain material, or private deployment configuration through the public site.
- Run SQLite production reads/writes on the NAS-native filesystem. Avoid direct live SQLite access through the macOS SMB mount while a NAS worker may write.
- Preserve existing crawler throttling, robots handling, authentication checks, locks, retry/backoff, review-required states, and role-scoped publishing.

## Current automated workflows

| Workflow | LaunchAgent | Schedule | Entry point | Production effect |
| --- | --- | --- | --- | --- |
| Guide Discovery V2 | `local.oursteps.guide-discovery` | 03:15 daily | `scripts/history_launcher.py guide-discover --max-pages 25 --max-minutes 20` | Discovers candidate historical TIDs only; resumable and bounded. |
| Incremental sync | `local.oursteps.incremental-sync` | 06:00, then every 2 hours through 22:00 | `scripts/update_now.py --now` | Authenticates, syncs current content, updates preview/publication using existing production workflow. |
| Historical backfill | `local.oursteps.historical-backfill` | 07:00, then every 2 hours through 21:00 | `scripts/history_launcher.py backfill --best-effort --now --max-threads 10 --max-minutes 45` | Bounded historical backfill. Successful completion proceeds through preview, public publish, then public healthcheck. |
| Worker status snapshot | `local.oursteps.worker-status` | Every 30 seconds | `scripts/worker_status_snapshot.py` | Read-only NAS worker-lock probe. Writes only sanitized owner-Control-Center status; no PID, command, path, host or secret fields are published. |

The schedules above describe the currently loaded Mac mini LaunchAgents. Do not silently replace them from an older installer template.

### Temporary historical-backfill acceleration — 2026-09-17

Historical backfill was raised from **5 to 10 threads per scheduled run** while keeping the existing 45-minute cap, two-hour cadence, request throttling, Busy/HTTP retry handling, authentication checks and worker/publish locks unchanged. This is a conservative throughput increase only; it is not authorization to increase request concurrency or weaken backoff. Observe roughly 24 hours of production runs before considering any further increase (for example 10 -> 15).


## Manual command collision guard

Manual archive/authentication launchers now run `scripts/archive_worker_status.py --guard` before starting. If the NAS `data/worker.lock` is held by an active archive writer, the manual command stops before authentication/network work begins and asks the operator to retry later. This prevents a manual update/auth/discovery/backfill from colliding with a scheduled Historical Backfill or Incremental Sync.

For an immediate read-only check, double-click `OurSteps Worker Status.command`. It reports `RUNNING`, `IDLE`, or `UNKNOWN`. The existing `Historical Backfill Live Status.command` remains the deeper progress monitor.

### Live Archive Worker status

The owner-only 8448 Control Center reads `/control-action/worker-status` every 5 seconds. A dedicated Mac LaunchAgent (`local.oursteps.worker-status`) refreshes the sanitized source snapshot every 30 seconds, independently of the long-running Control Center action runner. The page shows only `RUNNING` / `IDLE` / `UNKNOWN`, a bounded task label, elapsed/start time, next Historical Backfill time and snapshot timestamp. Status older than 90 seconds is forced to `UNKNOWN`. OStester/recent scope receives neither the Control Center assets nor this API result.

Manual `.command` archive/auth entry points also run `scripts/archive_worker_status.py --guard` before starting. If the NAS worker lock is busy or status cannot be safely confirmed, the manual write action is not started.

## Supported manual entry points

### Read-only / inspection

- `Historical Archive Status.command`
- `Historical Backfill Live Status.command`
- installed `oursteps-status`
- installed `show-missing`
- `python3 scripts/public_healthcheck.py` on the NAS
- normal `git status`, log inspection, JSON/SQLite read-only queries executed NAS-side

### Controlled write operations

- `Authenticate OurSteps.command` — updates the dedicated authenticated session only after identity verification.
- `Update OurSteps Now.command` — runs the normal incremental workflow immediately.
- `Historical Backfill.command` — bounded best-effort backfill using the existing launcher.
- `Discover All OurSteps Threads.command` and `Discover Remaining OurSteps Threads.command` — legacy/specialized discovery entry points; do not substitute them for Guide V2 without a demonstrated need.
- `python3 scripts/publish_public.py` — controlled atomic publication using existing validation/locks.
- `python3 scripts/publish_public.py --rollback` — publication rollback only; use after inspection of a bad release.

## Production publication chain

Normal successful paths converge on the same protected static publication layer:

1. private NAS archive state
2. generated preview/static candidates
3. `scripts/publish_public.py`
4. release validation and atomic current/previous switch
5. `scripts/public_healthcheck.py`
6. nginx serves the validated static release on `8448`

Historical backfill now performs publish + healthcheck automatically after a successful backfill. Do not add a second duplicate publish step to that scheduler.

## Operator rules

1. Prefer Terminal/Desktop Commander for status checks, logs, existing tests, read-only SQLite queries, launchd inspection, and other routine operations.
2. Use Codex for necessary code changes, cross-file diagnosis, refactoring, or genuinely new tests; do not spend Codex tokens waiting on commands or re-reading completed work.
3. Before code changes, inspect `git status`, `STATUS.md`, `PROJECT_HANDOFF.md`, this file, and the relevant implementation file.
4. Do not redo completed Search / Global Sorting UX, Guide V2, or historical scheduler work unless a regression is demonstrated.
5. After an important code or documentation change: run focused tests/validation, update the handoff/status documentation, commit, and push `main` so Codex and future sessions see the same checkpoint.
6. Never force ambiguous content complete merely to improve counts. Preserve `review_required` / partial states where evidence conflicts.

## Phase 1-4 close-out status

The four-stage close-out is **complete and production validated** as of 2026-09-16. Treat the phase headings below as the implemented architecture, not future work. See `CODEX_HANDOFF.md` for the concise code-assistant handoff.

Final production validation included:

- owner Control Center opened successfully on 8448 while the recent/tester scope remained isolated
- privacy-minimal article analytics recorded and published real article reads
- a real `publish_public` Control Center action traversed browser -> nginx -> internal API -> private queue -> Mac runner -> publish -> healthcheck and ended `succeeded`
- public healthcheck passed after the action, with a dated snapshot of 5,522 full / 3,414 recent-1y articles
- scheduled incremental sync continued operating during close-out; counts may therefore increase after this snapshot

Latest live-validated code checkpoint before documentation close-out: `3d5828b`.

## Control Center roadmap

The project close-out is intentionally staged:

### Phase 1 — Documentation & Tool Registry

Create and maintain this operations guide plus `config/tool_registry.json` as the machine-readable inventory of supported operational tools. Phase 1 changes documentation/metadata only and must not alter production behavior.

### Phase 2 — 8448 Control Center (read-only first)

Implemented as an owner-only static control surface rather than a new executable API service. Each public publish generates `control-center.html`, `control-center.css`, `control-center.js`, and a sanitized `control-center.json` snapshot into the full archive release only. The recent/tester scope does not contain these files, so the existing filesystem scope boundary returns 404 there. The snapshot exposes only allowlisted counts/state fields and scheduler descriptions; raw errors, paths, credentials, sessions, arbitrary SQL and shell execution are excluded.

The nginx allowlist must include the four control-center files. After this change is deployed, the existing `oursteps-public-web` container needs one restart/recreate to reload its nginx configuration; static publishing alone does not reload a running nginx process.

### Phase 3 — Article Analytics / 本站阅读次数

Add first-party archive reading analytics separately from source-forum view counts. The UI and data model must clearly distinguish OurSteps/source views from reads on this archive. Analytics must not weaken public/static isolation or leak reader identity unnecessarily.

### Phase 4 — Safe Action Buttons


Production validation note (2026-09-16): action requests must not gate on `$remote_user` in nginx rewrite phase. Basic Auth runs first; the internal queue-only API then independently enforces `X-Oursteps-Scope == full`. This preserves OSowner-only actions without the pre-auth 404 bug.
The validated implementation adds narrowly scoped action buttons backed by explicit registry actions. Every write action must define authorization, confirmation, locking/idempotency behavior, timeout/error reporting, and a safe failure mode. No arbitrary shell command field is permitted.

## Tool registry contract

`config/tool_registry.json` is a public-safe capability registry, not a credential store. Each entry declares:

- stable tool ID and label
- category and safety level
- read-only versus mutating behavior
- supported entry point
- whether it is suitable for the future 8448 control center
- confirmation/authentication expectations
- operational notes and protected boundaries

A registry entry does not by itself authorize execution. Phase 2/4 server code must additionally enforce its own allowlist and role checks.

## Stable completed checkpoints

- `87a592c` — preview global search and sorting UX; complete and production validated.
- `94043e4` — historical backfill automatically publishes and healthchecks after successful completion.
- `af4b006` — documentation checkpoint for historical backfill auto-publish; Phase 1 starts from this clean `main` state.

See `STATUS.md`, `PROJECT_HANDOFF.md`, `PUBLIC_PUBLISH.md`, and `AUTO_BATCH_OPERATIONS.md` for deeper implementation history.

## Phase 3 — Article Analytics implementation

First-party article reads are counted from a dedicated nginx access log mounted at `data/public-analytics/`. The log format is intentionally minimal: timestamp, article URI/TID, and HTTP status only. No IP address, Basic Auth username, User-Agent, Cookie, or session data is recorded. Only successful `GET /<tid>.html` rows count. The generated `article-views.json` is public-safe and contains TID-to-count mappings only; article pages render this as `本站阅读`, distinct from the source-forum `浏览` metric.

## Phase 4 — Safe Action Buttons

The 8448 owner Control Center now supports a deliberately narrow action queue. The browser never receives shell, SSH, Keychain, Docker, database or filesystem execution capability.

Allowed web actions are exactly:

- `incremental_sync`
- `guide_discovery_v2`
- `historical_backfill`
- `publish_public`

`rollback_public` remains Terminal-only because it is high impact. `authenticate` remains local-only because it is secret-bearing.

Execution path:

1. owner-authenticated 8448 Control Center submits a fixed action ID
2. nginx permits `/control-action/` only for the full/owner scope
3. internal `action-api` has no published host port and can only write the private `data/control-actions` queue
4. `local.oursteps.control-actions` on the Mac mini checks the queue every 60 seconds
5. the Mac runner uses its own hard-coded allowlist and existing supported launchers
6. only sanitized pending/running/succeeded/failed state is exposed back to the Control Center

The API requires a custom action header, rejects request bodies, de-duplicates an already pending/running action, and exposes no command output or private paths. The runner does not execute command text from the registry.

## Phase 4 queue permission fix

The action queue uses a private NAS directory with mode 777 and queue lock/job JSON files mode 666 so the capability-dropped internal action-api and the Mac-side runner can both update the same queue without Docker/SSH/secret privileges. The payloads contain only action/status/timestamps/messages and are never served as public static files.

## Final Phase 4 production validation

The safe-action path is production validated. A real owner-triggered `publish_public` job moved through pending -> running -> succeeded and completed the public healthcheck successfully.

Two live-only integration bugs were fixed during validation:

- nginx must not use a rewrite-phase `$remote_user` / `$archive_scope` `if` for action authorization; Basic Auth completes first and the internal API independently requires full owner scope
- the capability-dropped action API must not chmod a pre-existing shared queue lock it does not own; shared queue files use a deliberately narrow private queue protocol and are never public static assets

Do not weaken these boundaries or reintroduce direct web execution.

## Owner homepage Control Center entry

The full/owner archive homepage includes a `Control Center` button linking to `/control-center.html`. This link is injected by the public publisher only into the full-scope `index.html`; the generated recent/tester `index.html` explicitly removes it. Release validation enforces that new owner releases contain exactly one link and recent scope contains none. Older releases remain readable for rollback compatibility.
