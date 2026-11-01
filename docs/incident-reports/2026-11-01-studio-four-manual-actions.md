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
