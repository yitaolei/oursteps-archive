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

## 2026-11-02 05:27 AEDT overnight evidence and candidate-priority correction

- 02:15 scheduled batch: 11 HTTP fetches, 10 new stored snapshots, zero newly complete articles, `budget_reached`.
- 04:15 scheduled batch: 11 HTTP fetches, 10 new stored snapshots, zero newly complete articles, `budget_reached`.
- Read-only inspection demonstrated both batches attempted the same 10 TIDs, each returned `retry_later:incomplete_html` (incomplete/challenge pages); earlier due-first candidate ordering inadvertently prioritized these repeated unsuccessful retries ahead of new work.
- Corrected `oursteps/history.py` ordering to prioritize genuinely pending jobs first, then previously unqueued inventory threads, then eligible retry_later jobs. Retains existing retry policy, site robots window, shared writer lock and fixed throughput budget.
- Ran a bounded live Studio recovery batch inside the published visit window. During execution at 05:29 AEDT, Studio SQLite count had already advanced from 15,162 to 15,167 complete articles. Final after-run counts and release verification must be recorded separately.

## 2026-11-02 05:32 AEDT historical throughput budget increase

- Earlier 02:15 and 04:15 cycles each attempted 10 repeatedly challenged threads, with 10 new snapshots and zero complete articles. A corrected pending/new-before-retry priority produced five complete articles from 10 started threads, 24 fetches and 23 new snapshots in 119 seconds. The post-publish check passed and the new complete count was 15,167.
- The historical scheduler was overly restricted to 10 started threads and 20 minutes per run, four times daily. Assuming a 50 percent completion yield, the 19,786 incomplete inventory backlog would require roughly 989 days at the old maximum of 20 complete/day (illustrative only).
- Increased Studio historical cycle to 60 started threads and 45 minutes maximum per run; worker process timeout to 50 minutes. Manual action wrapper timeout increased to 60 minutes. No source-request pacing, robots permissions, backoff, single-writer authority or SQLite integrity rules were relaxed. The new four-run daily theoretical cap is 240 started threads, not 240 completed articles; actual duration and completion yield may be lower.
- This higher budget has passed syntax verification; an in-window full-scale run must still confirm sustained output and absence of source rate-limiting before raising it again.

## 2026-11-02 07:39-07:45 AEDT incremental-sync zero-today incident

- User observed Control Center button reported succeeded while TODAY 2026-11-02 showed zero. Confirmed 07:23 `data/sync-summary.json` status `retry_later`, zero discovered, `incomplete_html`, browser fallback TID 1905222. The entrypoint `studio_primary_cycle.py` printed return code but did not exit with it, yielding false success (0) to manual worker and suppressing browser recovery.
- Fixed Studio cycle to propagate nonzero return code and recognize incomplete `sync-summary.json` even when the thread never entered the `daily-challenge` table.
- Fixed manual worker to consider CLI exit 2 for retry_later, extract the latest safe TID from the structured browser fallback URL, attempt one bounded authorized local browser import, and report partial instead of false success on outstanding challenge pages. Added automatic partial publish/HTTPS refresh for already verified articles.
- Local browser successfully imported TID 1905222 as complete and flushed preview. Other new TIDs 1905221, 1905220, 1905219 still saw incomplete HTML; do not claim discovery_complete.
- Manually published local release fb4cf81ad5ed4331fba50881ece236babc470fc5bb0c7c80af5f57c387571cd2, post-publish PASS with 15,276 complete and 4,459 recent; Studio index contains 1905222 on 2026-11-02. This is a verified partial recovery, not yet full daily discovery.

## 2026-11-02 07:46 AEDT multi-post challenge starvation

- Source-site screenshot shows numerous 2026-11-02 posts while Studio TODAY page only one item. The old daily incremental algorithm stopped directory scanning when the fourth challenged first page appeared (`len(deferred_threads)<3`), despite a valid authenticated directory listing. Browser rescue was also bounded to only one article per invocation.
- Patched `oursteps/sync.py` to queue every `retry_later:incomplete_html` first-page candidate as `daily-challenge`/`retry_later` and continue scanning the remainder of the verified daily listing. It must not mark challenged HTML as complete or bypass robots rules. This preserves 'publish all verified, defer unresolved' behavior without imposing an arbitrary limit of three challenged articles.
- Compiled Python module. Attempted immediate live daily cycle but Studio 07:45 scheduled historical worker already held the shared production lock; no concurrent database writer was forced. The corrected multi-post daily scan remains **pending live run after the lock releases**.

## 2026-11-02 07:53 AEDT owner manual sync priority over scheduled historical backfill

- Keep the single Studio SQLite writer lock: manual priority must not mean two concurrent writers.
- Studio manual API now creates a short-lived `data/studio-manual-priority.json` marker when an OSowner initiates Incremental Sync Now.
- Historical worker checks that marker at the next thread boundary and yields `manual_priority_yield`, without interrupting a network response or active SQLite write. Its outer cycle writes the usual receipt and skips the now-unnecessary historical publish, allowing the owner sync to acquire the lock.
- Manual sync worker sets `OURSTEPS_MANUAL_PRIORITY=1`; Studio primary cycle waits for the lock for up to 180 seconds instead of immediately failing. Once the exclusive lock is acquired, the marker is removed, and normal sync/publish proceeds.
- All five changed Python modules passed AST/py_compile checks. The currently active 07:45 historical process started before this patch and cannot cooperatively yield mid-run. Full live preemption must be validated with a *new* historical worker instance; do not claim it has already succeeded.

## 2026-11-02 08:01 AEDT urgent current-day batch recovery

- OSowner requested that Incremental Sync Now take precedence. Actual manual action POST to the running owner API succeeded at 08:00:40 and created PID 54790; manual-priority marker is present. However, a historical worker that started at 07:45 before the cooperative-yield change still owns the shared write lock; the manual sync is correctly queued and must not concurrently write the SQLite database.
- Updated manual worker from single-item browser fallback to bounded 3 rounds of up to 20 verified `daily-challenge` first-page imports per round, using existing logged-in Chrome snapshot validator, preview flush and repeated daily scan. This patch is effective for new worker instances; running PID 54790 loaded the older code at start. A fresh live batch run after lock release is required before declaring the daily listing fully recovered.

## 2026-11-02 08:05 AEDT daytime historical page cap

- User directed that normal daytime historical archive work must not process long threads beyond ten pages; allow full-length thread completion overnight instead.
- Added `page_limit` to `oursteps.cli.run`: when the next thread job exceeds that limit, exit the per-thread loop as `page_limit_reached` before any fetch. Existing parsed pages/jobs persist, and incomplete threads remain incomplete rather than being misreported as archived.
- Historical backfill uses `Australia/Sydney` local time. From 01:00 up to 06:00 it allows the existing full-thread policy; outside those hours it enforces a 10-page-per-thread ceiling, skipping existing threads with pending page >10. All source robots restrictions continue to apply; the nighttime exception never bypasses source policy.
- Passed py_compile; next run is required to verify end-to-end performance, since in-flight workers retain old imported modules.

## 2026-11-02 long-thread time-window amendment

- Per owner: long-thread eligibility begins at 22:00 Sydney local time rather than 01:00. History page cap is disabled between 22:00 and 06:00 local, and ten pages otherwise.
- Source robots visit-time remains independently enforced; eligibility does not authorize network activity outside the source permitted UTC hours.

## 2026-11-02 08:16 AEDT regression acceptance gate

- Added `scripts/studio_incremental_acceptance.py`, a read-only, non-network Studio production acceptance check for the current Sydney calendar day. It checks SQLite quick_check, nonzero discovered daily membership, all daily members complete, empty unresolved daily-challenge queue, fresh sync receipt status=success/discovery_complete=true/count agreement, and the currently published local release HTML containing every complete daily TID.
- Script returns exit 1 on any missing or partial state; unlike a green command exit, it will never call zero new articles a successful current-day acceptance. Does not alter writers, releases, NAS state or source access rate.
- Real Studio check on 2026-11-02: PASS; 16 discovered / 16 complete, no unresolved challenges, zero missing in current release, SQLite `ok`.
- Remaining improvements: automated invocation after every release, synthetic failure-path test cases, and non-destructive scheduling preemption integration tests before shipping further changes. This check cannot independently know whether the source site posted more articles than its current discovery receipt; source-list completeness still requires separate comparison.
