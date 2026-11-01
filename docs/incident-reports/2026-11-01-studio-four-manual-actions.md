# OurSteps Studio manual controls migration - 2026-11-01

Four manual actions have been staged on Studio HTTPS 18751: Incremental Sync Now, Guide Discovery V2, Historical Backfill, Publish Public Release.

## Architecture

- Browser connects to the existing OSowner-protected Apache test service at 18751. `/studio-action/` is proxied to an HTTP server bound to 127.0.0.1:18752, never advertised on the LAN directly.
- Fixed action allowlist, empty-body POST plus required action header, no shell command submitted through HTTP, and one shared manual action receipt.
- Incremental Sync Now uses Studio `studio_primary_cycle.py` plus bounded local browser recovery of challenge-deferred first pages, not legacy NAS SSH executors.
- Guide Discovery V2: Studio-local guide discovery, at most 5 pages / 5 minutes, shared Studio cycle lock.
- Historical Backfill: Studio-local bounded backfill cycle, at most 10 threads / 20 minutes, shares primary cycle lock and follows with local publish and HTTPS refresh.
- Publish Public Release: Studio local immutable publisher, post-publish check and HTTPS refresh; never writes NAS SQLite.
- Manual status stored in `data/studio-manual-sync.json`; output logged under `data/logs/studio-manual-sync.log`. Periodic autonomous agents continue independently.

## Evidence

- All four control buttons confirmed present in rendered Studio Control Center.
- Apache syntax check passed; anonymous GET requests for the three new action routes returned 401.
- Publish action triggered through local backend, transitioned running -> succeeded, exit 0, `post_publish_check: pass`, HTTPS refresh OK. Release unchanged at 15,162 complete articles / 4,468 recent articles.
- Incremental Sync Now was previously verified end-to-end with TID 1905193, browser fallback and successful local publish.

## Remaining acceptance and safety

- Authenticated browser click tests and OStester denial must still be performed with the respective accounts; do not infer role isolation solely from anonymous HTTP 401.
- Guide Discovery and Historical Backfill new buttons have NOT yet passed a full manual click/run acceptance. Do not mark as fully verified.
- UI manual status is a single latest-job record, not yet a durable queue/history; no overlapping manual jobs are launched when a running PID is live.
- Confirm all scheduled jobs and optional browser fallback run in the correct access window. Do not bypass site rate limits or challenges.
- NAS 8448 has NOT switched. Old NAS writers remain disabled and NAS is retained for rollback.

## Manual acceptance update, 2026-11-01 15:25 AEDT

- Guide Discovery V2 was invoked through the Studio local action API. It transitioned `running` to `succeeded` (exit 0), reporting `pass_complete` and no new discovery work because the current guide pass had reached its end.
- Historical Backfill was invoked through the same action API. It transitioned to an exited worker state and passed an unchanged release validation. The worker reported `waiting_window`, zero new snapshots and zero new complete threads; this is a valid access-window pause **not proof of actual backfill throughput**. The manual worker now identifies this condition as `deferred` rather than `succeeded` on future runs.
- Studio SQLite `PRAGMA quick_check` returned `ok`, with 15,162 complete articles. NAS source DB modification time remained 2026-11-01 10:07:30 AEDT.
- Still required: real `OStester` denial test; browser click tests for all actions; a manual backfill run during a permitted window to demonstrate newly archived articles; stale PID and multi-worker fault tests.

## 15:26 AEDT reliability follow-up

- All three Studio scheduled services (primary cycle, backfill, health) and the localhost-only manual API were loaded. NAS original SQLite timestamp still matched the frozen 10:07:30 AEDT baseline.
- Reviewed owner-only Apache proxy: `^/studio-action/(?:status|incremental-sync|guide-discovery|historical-backfill|publish-release)$` is Basic Auth `Require user OSowner`. Anonymous and role-specific outcomes are distinct; live OStester negative test is still required.
- Added `studio_manual_sync_api.py` stale-running status detection: if the stored worker PID no longer exists, the job is marked failed rather than staying indefinitely `running`. Source compiled, service restarted, existing finished receipt remained readable. The crash path still needs a controlled fault test.
- Manual backfill's historical success receipt was created before the `waiting_window` -> `deferred` patch, so it still says `succeeded`. Future receipts should report the corrected state; do not rewrite old evidence retroactively.

## 15:30 AEDT access-window alignment and auth review

- Found `oursteps.fetch.Fetcher` enforces the 14:00-22:00 UTC source-site access window. At 15:30 AEDT (04:30 UTC) the user-facing manual backfill cannot truthfully demonstrate throughput; its `waiting_window` result is expected. No bypass was used.
- Replaced Studio `local.oursteps.studio-primary-backfill` StartInterval=7200 with four local Sydney calendar starts: 02:15, 04:15, 06:15, 07:45. These fall in 14:00-22:00 UTC in both AEDT and AEST. The LaunchAgent was reloaded and schedule entries verified.
- Anonymous requests to full archive index, TID 1905193, first-page review, Studio health JSON, Studio manual action status and action path, and recent-one-year index all returned HTTP 401. Apache syntax and `Require user OSowner` rules were reviewed.
- Real `OStester` authorization denial and `OSowner` acceptance tests are **not yet validated in this run**; passwords were not requested or stored. Actual in-window throughput still needs an observed scheduled batch with positive fetch/complete counters.
- Frozen NAS SQLite baseline modification time remained 2026-11-01 10:07:30 AEDT.

## 15:38 AEDT due-first backfill candidate optimization

- Verified OurSteps robots.txt directly earlier today: Request-rate 1/5, Crawl-delay 5, Visit-time 1400-2200; keep all source policy, pacing and network-denial safeguards.
- A read-only Studio SQLite audit found 19,791 incomplete inventory threads, including 19,612 never-queued records and 142 distinct incomplete TIDs with a currently due pending/retry job. Many early sorted entries were already `not_found` or `auth_required` and could not be processed.
- Updated `oursteps/history.py` candidate enumeration: select due pending/retry TIDs and never-queued inventory TIDs; prioritize jobs already due, then unqueued records. Do not repeatedly scan permanently failed or not-yet-due jobs as candidates. Existing per-thread worker locks, budget, robots checks and retry conditions are unchanged.
- Verified SQL against read-only production SQLite and compiled module. Next in-window live scheduled run must verify throughput; this is a scheduling/candidate optimization, **not yet evidence of successful new fetches**.

## 15:40 AEDT avoid unnecessary release work outside visit window

- Verified the new scheduled historical backfill calendar was loaded but had not yet run (`runs = 0`), as the local time was outside the source site's publicly declared Visit-time. No throughput claim is warranted yet.
- Added a guarded early return in `scripts/studio_primary_backfill_cycle.py`: when the worker explicitly reports `waiting_window` and before/after Studio production metrics are identical, record `STUDIO_BACKFILL_DEFERRED_WINDOW_NO_PUBLISH` and skip preview generation, release publish, and Apache refresh. Other outcomes retain the original publish/validation flow.
- Tested the branch on the Studio runtime. Worker returned `waiting_window`, zero new snapshots and zero new complete threads, then emitted the expected no-publish message. Python syntax validation passed; existing Studio website and NAS database were not rewritten by this no-op.
- Positive throughput still requires one observed run within the permitted window. Maintain rate limiting and website robots constraints.

## 15:53 AEDT durable per-run throughput receipts

- Added append-only `data/studio-backfill-runs.jsonl` to the Studio historical cycle. Each completed worker invocation records starting timestamp, duration, before/after complete and snapshot counts, number of new complete articles, new snapshots, source network fetches, started threads, worker exit and worker status. The file is Studio-local runtime data, **not** included in Git.
- Verified the new receipt writer with a real outside-window backfill cycle: `waiting_window`, 0 network fetches, 0 new snapshots and complete threads. The no-op publishing guard still avoided release regeneration.
- This makes scheduled run outcomes independently auditable after the first allowed 02:15 AEDT cycle; it does not assert that a future run has already occurred or gained articles.
