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

## 2026-11-02 08:20 AEDT restored owner Archive Worker live panel

- Root cause: Studio branch in `oursteps/control_center.py` explicitly replaced the original Archive Worker polling UI with the placeholder that its real-time endpoint was disabled.
- Added read-only `scripts/studio_worker_status.py`: samples local Studio Python worker process, start/elapsed, IDLE/RUNNING, and upcoming configured historical schedule, without invoking NAS SSH or touching database writers. The owner localhost action API exposes `/studio-action/worker-status` via the existing owner-protected Apache proxy.
- Studio Control Center now renders the original worker table with live 5-second polling; API returned IDLE on live validation. Published release `6e15cca32dc7b7aec8a9a0778de48d8c46ac2c5fd23ff38b307ff5e1348ff44a`, post-publish check PASS, 15,296 complete articles. New release JS contains the live endpoint and worker rendering.
- Follow-up: synthetic RUNNING process detection tests, status correctness during manual/automatic overlap, and confirm authenticated browser rendering. Current next scheduled time is computed from configured historical slots; actual LaunchAgent state should be independently checked before trusting it as a scheduling guarantee.

## 2026-11-02 08:21 AEDT live worker route completed

- Initial deployed owner UI and loopback API worked, but 18751 HTTPS returned 500 because the Apache owner-only LocationMatch allowlist omitted `worker-status`. Corrected `studio-apache-private/httpd-auth-test.conf` to admit `/studio-action/worker-status` under the existing authenticated action proxy. Apache syntax test PASS and graceful reload completed; unauthenticated 18751 access returns expected 401.
- Verified Studio worker probe reports IDLE and scheduled next run; read-only daily acceptance remains PASS (16 of 16 current-day articles). Authenticated browser-rendered panel still requires owner visual confirmation; do not claim that credentialed UI access was independently tested.
- This is a machine-local private Apache config edit, not a Git-tracked credential/config export. Back up and incorporate the whitelist into any future Apache config provisioning to avoid regression.

## 2026-11-02 08:25 AEDT production wiring regression gate

- Added `scripts/studio_production_regression.py`, a non-destructive regression check for owner-only Apache worker-status routing, safe Apache syntax, the live local status API, manual priority signal, historical safe-yield, manual lock wait, daytime ten-page limit and bounded batch browser recovery.
- All 10 checks passed on the Studio runtime. These are wiring / live API smoke checks, **not** synthetic concurrent-writer or full end-to-end preemption tests; those still need controlled tests. Current 16/16 daily content acceptance also passed earlier today.
- Refrained from invoking legacy `studio_apache_staging_setup.py`: it provisions isolated older staging, not the current 18751 authenticated production config, and would risk overwriting live owner restrictions.

## 2026-11-02 11:50 AEDT NAS 8448 retirement readiness review

- Owner has authorized permanently retiring the legacy NAS 8448 web endpoint. No NAS service changes yet: Studio 18751 remains authenticated and fully independent but legacy 8448 still returns a 401 challenge.
- Live Studio checks: local SQLite quick_check OK; complete 15,296; 2026-11-02 16/16 discovered and complete, unresolved challenges 0; last manual incremental succeeded; daily acceptance PASS, configuration smoke gate 10/10 PASS; NAS database file mtime frozen at 2026-11-01 10:07:30.
- Added `scripts/studio_nas_retirement_gate.py` to fail closed. Five Studio-side checks pass. Five cutover operational receipts remain absent: externally reachable Studio replacement; identification of exact NAS reverse-proxy/8448 web target; owner/tester end-to-end authentication after transition; a tested reversible web-only stop; and verified 8448 route retirement. These five are deliberately unresolved, not evidence that the tests have failed.
- MUST NOT disable NAS unrelated Docker services, overwrite NAS SQLite, expose Studio owner control to public, or claim 8448 routed to Studio without validated route configuration. The owner can opt to retire 8448 with 18751 as the replacement once its reachability for the intended audience and the NAS-only web-service stop/rollback are verified.

## 2026-11-02 11:52 AEDT verified NAS legacy service mapping

- Read the frozen NAS shared config `compose.public.yaml` without changing NAS data or containers. The legacy OurSteps web is dedicated container `oursteps-public-web`, nginx:alpine, exposed on NAS host `18080:8080`, with `restart: unless-stopped`. Its publication mount is `./public-site` read-only and config is `./nginx-public-stable.conf` read-only. Separate `oursteps-action-api` container exists; do not stop it blindly unless ownership is verified.
- Confirmed externally the NAS 8448 endpoint still responds with HTTP 401 while Studio 18751 also responds 401 to unauthenticated requests. This alone does NOT verify the Synology DSM reverse-proxy mapping; likely 8448 -> 18080 but requires NAS configuration evidence.
- An attempted SSH connection to guessed address 192.168.0.100 timed out; that guessed address is NOT verified as NAS. Do not use guessed NAS IPs or blindly run docker stop.
- Targeted retirement operation once NAS admin access and DSM mapping are confirmed: disable restart for `oursteps-public-web`, stop only this exact container, confirm 18080 no longer serves, disable only the matching DSM 8448 reverse-proxy rule, and validate Studio 18751 still passes OSowner/OStester permissions. Preserve NAS data for rollback; record exact previous compose and proxy configuration.
- No NAS service was stopped. External replacement reachability and authenticated post-change testing remain outstanding gates.

## 2026-11-03 08:55 AEDT historical backfill starvation correction

- Overnight four batches processed 240 thread-attempts, produced 334 snapshots, but only 1 new complete article. Current complete=15,310, inventory=34,323, 19,630 eligible incomplete threads; current-day November 3 sync 13/13 PASS.
- Investigation found selector in `oursteps/history.py` ranked all due `pending` jobs above all previously unstarted inventory. This creates a starvation pattern where costly multipart continuations can dominate runs and prevent newly queued one-page inventory from becoming complete.
- Changed selector to unstarted inventory first, due pending continuations second, retry_later last. No DB changes or source timing/politeness changes. The existing daytime 10-page/overnight long-page policy remains unchanged.
- Verified Python syntax and read-only equivalent selector against live SQLite: first 60 candidates all `unstarted`. Actual network throughput after deployment NOT YET measured; historical next run is during next permitted schedule. Revisit fairness between unstarted and partial cohorts if all-unstarted priority slows long-thread completion.

## 2026-11-03 09:10 AEDT resumable five-page historical slices

- Asked Codex gpt-6.1-terra to implement in the isolated Git checkout with synthetic SQLite regression tests. Studio history work now uses `--per-thread-page-budget 5`; `oursteps.cli.run` counts successfully processed pages per TID, stops with `thread_page_budget_reached` while leaving the next page pending for later continuation. Existing daytime page-10 limit remains independent.
- Codex corrected a page-budget/daytime-limit boundary test during development. Final `test_history*.py` suite: 27 PASS with available local dependencies, plus diff check passed. No source rate limits, historical visit-time rules, NAS data or public port changed.
- Commit `063c593` pushed to existing isolation branch. Safely acquired Studio production cycle lock, saved `.before-page-slice` backups for four active source scripts and deployed them. Compile PASS, daily current-day 13/13 release acceptance PASS and production smoke 10/10 PASS. Real historical throughput improvement is NOT YET verified until future permitted scheduled runs.

## 2026-11-04 17:38 AEDT P0 historical worker regression and repair

- User requested the missed Nov4 result. Real Studio batch receipts for 02:15,04:15,06:15,07:45 each had new_complete=0, new_snapshots=0, worker_exit=1, elapsed_seconds=0.05-0.08. Their `worker_state=budget_reached` was false: it was copied from stale `backfill-performance.json` of Nov3. LaunchAgent last exit=1.
- Root cause definitively reproduced in `~/Library/Logs/oursteps-studio-backfill.err.log`: four `SQLite worker must run on NAS native filesystem`. Prior page-slice deployment copied isolated Git `scripts/history_worker.py` (old unconditional macOS rejection) over the Studio-specialized production entrypoint. This was a deployment preservation defect, not evidence of source or long-thread slowness.
- Restored Studio's `allow_local_authority(a.data)` guard from `.before-page-slice` backup, retaining `--per-thread-page-budget` argument and forwarding. Preserve rejected script as `.bad-nas-guard-20261104` for forensic diff.
- Fixed `studio_primary_backfill_cycle.py`: if subprocess return code nonzero, receipt worker_state now `worker_failed_exit_<rc>` and cannot reuse stale success state. Extended regression smoke gate to ensure macOS authority guard, five-page budget wiring and truthful failure reporting.
- Verified Python compile, history regression 27/27 PASS, Studio regression 13/13 PASS; launched the actual macOS history_worker `status` path against a temporary HOME-local SQLite data directory, exit=0, no network or production data writes. Git commit b671595 pushed.
- Separate November 4 daily acceptance is FAIL due to challenge-queued TID 1905316, partial, page 1 success and page 2 pending, not listed in daily_members. Daily members 22/22 complete, published, but the challenge queue must be reconciled. Do not conflate with historical worker failure.
- No historical network backfill run was made outside the source permitted visit-time. Next scheduled real backfill needed for throughput evidence; NAS 8448 remains untouched.

## 2026-11-04 17:43 AEDT independent Codex adversarial P0 audit and remediation

- Requested independent read-only Codex review of actual Studio runtime, exact LaunchAgent, Git source parity, Python dependencies, historical fairness and stale performance receipts. Codex returned **FAIL** for clean Git deployment (`scripts/studio_local_authority.py` missing), performance freshness and intra-lane starvation; next real 02:15 completion remains UNKNOWN. It verified the configured 02:15 time falls inside source robots UTC permitted window.
- Fixed missing Git dependency in commit `e661545`. Subsequent independent `scripts/history_worker.py status --data <HOME temporary dir>` from the isolated Git checkout exited 0 with synthetic identity config; no live network or production DB writes.
- Codex built fail-closed backfill receipt classification in its separate `/private/tmp/oursteps-p0-backfill-fix` working clone, 6 fault-injection tests PASS, 54 historical tests PASS. Accepted only current-run `backfill-performance.json` timestamps and valid counters; nonzero exit, stale/missing perf, no work, subprocess timeout or launch failure now recorded explicitly and block follow-on publish. Preserved explicit waiting-window/manual-owner yield behavior. Commit `080ae1f` pushed to existing isolated branch.
- Installed receipt hardening into Studio after acquiring production cycle lock with pre-change backups; module Python compile PASS, production smoke 13/13 PASS, Archive Worker IDLE. These checks DO NOT prove tomorrow's network response or successful archive throughput.
- Remaining architecture findings: intra-lane TID selection uses high-TID ordering without durable per-lane cursor, potentially starving older threads; the Studio runtime still differs from Git in several publishing modules and the locality guard cannot fully rule out network volumes in HOME. These are not cleared by this P0 repair; handle in scoped follow-up before claiming complete reproducible deployment.
- NAS 8448 left online. No historical network crawl executed outside robots visit-time.

## 2026-11-05 05:48 AEDT historical JS-challenge guard

- Production baseline: Nov 5 02:15 + 04:15 backfill produced 121 new snapshots from 120 thread attempts, 0 new complete, worker exit 0, state budget_reached. Stored HTTP 200 pages are single-script challenge bodies; 331 jobs were retry_later:incomplete_html. Complete=15334, inventory=34323, SQLite quick_check=ok.
- Codex independent read-only forensic audit of saved Studio snapshots: 40 distinct failed pages across both batches were 2.2-3.3 KB, one CLOSED script, no other DOM/text, all with location; 10 valid complete pages had normal HTML. 50 successful-page truncation negative cases had no matches with conservative structural detector. A previous new detector matched only 23/40 and was rejected; revised detector was tested directly on actual Studio files and matched 40/40 with 0/60 normal/truncated false positives.
- Codex read-only counter-audit identified three defects in initial prototype: legacy browser bypass could continue; small batches reset evidence; trivial script location false positives. Fixed all in isolated Git worktree, with per-TID durable 1h evidence, nonzero backfill worker exit on historical hold, historical-only browser import guard, stricter >=2KB closed-single-script content and negative cases.
- 85 targeted tests PASS; full suite baseline vs modified had exactly same 6 failures and 13 errors (environment/legacy tests), no newly failing named tests. Git branch fix/studio-historical-challenge-hold-20261105 commit 6194d1a; additional 3 Studio regression checks commit 29051b1.
- Scoped production installation of SIX existing files only: oursteps/fetch.py,cli.py,history.py; scripts/history_worker.py,studio_backfill_performance.py,studio_primary_backfill_cycle.py. Acquired studio-primary-cycle.lock and worker.lock, backed each file up with .before-js-hold-20261105 suffix, installed, byte-hash verified, compiled PASS. Installed regression script separately under cycle lock with .before-js-hold-check-20261105 backup. Production regression 16/16 PASS; SQLite quick_check ok, no new hold set at deployment, global halt empty. Did NOT deploy legacy NAS/browser files or experimental Studio daily browser changes.
- Critical limitation: this guards against repeated JS-only responses and prevents wasted historical requests; it does NOT solve challenge rendering, bypass 401/403 or create new complete threads by itself. Legacy historical browser site_hold dated 2026-10-31 09:30 remains unchanged; most recent stored robots snapshot HTTP 403. No manual override performed, NAS 8448 preserved.
- Planned verification: first scheduled true backfill Nov5 06:15; hourly read-only checks 06:45/07:45/08:45 AEDT plus existing morning check, always report exact current run counts and worker_exit/state to user. Do not call this throughput success until complete threads actually increase.
