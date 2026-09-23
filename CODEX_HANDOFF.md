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

The production historical-backfill LaunchAgent is now deliberately set to `--max-threads 20` after repeated 15-thread production runs remained far below the 45-minute cap. The 45-minute cap, two-hour schedule, crawler throttle, retry/backoff, locks and publication chain are unchanged. Treat 20 as the current observation trial; do not raise it again or increase concurrency without reviewing real completion time, Busy/500/timeout rates and scheduler overlap first.
Private backfill throughput telemetry is recorded in `data/backfill-performance.json` plus append-only `data/logs/backfill-performance.jsonl`. It is counts/timings only and should be used to quantify requests-per-completed-thread plus crawl/preview-build/throttle/network/parse/persistence time before further tuning.
## Manual worker collision protection — 2026-09-18

Manual `Update`, `Authenticate`, Historical Backfill, and discovery `.command` launchers now call `scripts/archive_worker_status.py --guard` before doing work. The guard probes the NAS-native `data/worker.lock`; when a scheduled archive writer is active, manual work stops before authentication/network access. `OurSteps Worker Status.command` is the one-shot read-only status entry point. Preserve this guard when changing manual launchers.

## Live worker status + manual guard — 2026-09-18

Owner Control Center now has a near-real-time Archive Worker panel. `local.oursteps.worker-status` runs every 30 seconds and writes only a sanitized status snapshot; `/control-action/worker-status` is owner/full-scope only, and stale snapshots (>90s) become `UNKNOWN`. The web payload must never expose PID, command lines, paths, hostnames, credentials or session details. Manual archive/auth `.command` entry points also guard against an active NAS worker before starting. Keep this independent from the long-running action runner so RUNNING status continues to refresh during 20–45 minute jobs. The Control Center Historical Backfill action is aligned with the production scheduler at `--max-threads 20 --max-minutes 45`.


## Historical Backfill Throughput V2 Phase A — 2026-09-19

Historical Backfill now persists eligible attempted TIDs in the NAS SQLite `settings` row `historical_preview_pending_tids` before crawling and renders only completed pending TIDs through staged `preview_batch.build_batch()`. Partial/retry TIDs survive restarts; successful incremental render clears only rendered historical TIDs. Stage failure is fail-closed and must not auto-fallback to a full preview build.

Measured 09:00 baseline: 15/15 completed, 23 network fetches, 1.533 requests/completed thread, 244.707 s crawl, 1,473.144 s full preview. Real 15-TID incremental preview: 11.953 s. Do not raise request concurrency until post-Phase-A production telemetry shows the next bottleneck. See `BACKFILL_THROUGHPUT_V2.md`.

## Throughput V2 Phase B pacing telemetry — 2026-09-19

17:00 production evidence: 13/15 completed, 687.772 s crawl, 585.995 s aggregate throttle, 7.705 s network, 27.960 s parse, 17.249 s persistence, 16.772 s incremental preview, 2 network errors, 1.385 requests/completed thread. Post-run `adaptive_delay` remained 27.91 s and `pause_until` had expired. New telemetry splits base pacing, adaptive pacing and global backoff without changing any pacing/backoff behavior. Do not increase concurrency or request rate; collect subsequent production evidence first.

19:00 clean run closes Phase A: 15/15 completed in 143.893 s crawl / 165.475 s total; zero network errors/timeouts; 24.008 s throttle = 0.542 s base + 23.466 s adaptive + 0 s backoff; 21.284 s incremental preview; pending preview TIDs returned to zero. Adaptive pacing is not a structural bottleneck when OurSteps is healthy.

Phase B now instruments public-release timing only: previous validation, prepare/source hashing, full stage, full validation, recent-1y stage, final validation, finalize/switch, total publish, plus `HEALTHCHECK_SECONDS`. Release hashes/manifests are unchanged. Wait for the first changed production publish with these metrics; if publish/healthcheck are minor relative to crawl, mark Phase B DONE and move to Phase C. Do not optimize release validation or add concurrency before that evidence.

Production measurement at 5,811 articles found healthcheck 66.536 s and unchanged publish 24.904 s, with 19.070 s in previous/current validation. Healthcheck was doing a redundant second full scan of current. The safe simplification keeps `validate_saved(current)`, explicit DB/current TID + recent-policy parity, and full `previous` rollback validation. Production recheck PASS: 37.741 s healthcheck (~43% faster). Do not weaken previous-release validation without separate evidence and review.

## Phase B DONE / Phase C schedule diagnosis — 2026-09-20 21:00

21:00 production is clean: 15/15 completed, 147.782 s crawl / 159.810 s worker total, zero network errors/timeouts/backoff, pacing split 9.275 s base + 2.810 s adaptive, 11.898 s incremental preview, zero pending TIDs. The changed release published 5,955 full / 3,835 recent articles in 42.802 s; post-publish check passed in 0.015 s; standalone full healthcheck passed in 39.269 s. Phase B is closed.

Phase C diagnosis: stored robots still says `Request-rate: 1/5`, `Crawl-delay: 5`, `Visit-time: 1400-2200` UTC, which is 00:00-08:00 AEST on 2026-09-20. Historical backfill remains on the 07:00,09:00,...,21:00 cadence with the pre-existing `--now`; this checkpoint changes only the cap from 15 to 20. Do not alter request rate/concurrency. The next structural Phase C scheduling task is to robots-align scheduled runs and remove scheduled `--now`; only after that should a dynamic work budget replace the fixed cap.


## Adaptive pacing recovery — 2026-09-20

07:00 completed 15/15 with no network errors/timeouts but still spent 57.886 s in adaptive pacing because yesterday's penalty decayed too slowly. Fetch success decay is now health-sensitive: HTTP 200 + `error_streak=0` uses 0.8 retention; recovery/non-200 keeps 0.9. This never bypasses robots/base pacing and does not alter error escalation, backoff, concurrency, or locks. Validate against the next clean production backfill before any further pacing change.

## Throughput V2 persistence simplification — 2026-09-20

Latest clean run: 15/15, 0 errors/timeouts, 219.676 s crawl, 49.608 s adaptive pacing, 27.627 s persistence, and 24.465 s SQLite commit time across 76 commit calls. Commit time is now the dominant persistence cost.

A narrow optimization skips `Store.set_setting()` writes when the persisted string value is already identical. This primarily removes repeated successful-page `error_streak=0` transactions while preserving immediate durability for changed/missing settings. Focused tests cover the no-op commit behavior and adaptive-decay rule. Next measure production commit-count/time; only then consider deeper transaction coalescing. Publish + immediate healthcheck duplicate validation remains the other structural target.

## Publish/healthcheck deduplication — 2026-09-20

Backfill previously spent about 71.8 s publishing and then another 37.7 s immediately revalidating current+previous with `public_healthcheck.py`. Because `publish()` already fully validates the old release, staged full release, and final full+recent release before switching live pointers, that immediate full rescan was redundant.

`publish_public.py` now runs `post_publish_check()` after non-rollback publish. It verifies current/previous pointers, manifest presence, manifest digest/release identity, and published article/recent counts. `history_launcher.py` no longer chains `public_healthcheck.py`. The standalone full healthcheck is intentionally unchanged for independent integrity sweeps. Focused release tests and py_compile pass.

## Final publish validation reuse — 2026-09-20

`publish()` no longer re-hashes the entire top-level stage after `stage_recent()`. It retains the first full validation result, validates only `recent-1y/`, cross-checks shared recent files and search-index subset against the validated full release, and merges recent hashes into the deterministic manifest. This removes the second ~5.9k-article scan without weakening the pre-switch full validation or release identity. Focused public-release tests pass.

## Changed-publish old-release prescan removal — 2026-09-20

Changed production publish previously paid ~18–19 s to `validate_saved(current)` before staging. It now loads old metadata only, reuses hardlinks by manifest match, then requires the fully validated staged top-level hashes to equal current authoritative `source_hashes`. Tampered/stale hardlinks therefore fail before pointer switch. The unchanged fast path still validates current fully; rollback still validates current/previous fully. Focused tamper/idempotency/fail-closed tests pass.

## SQLite save/status commit reduction — 2026-09-20

`save_page()` and its per-TID completeness refresh now share one transaction; snapshot remains separately durable. Normal successful page persistence therefore drops from 3 commits to 2 before considering no-op setting elimination. Focused persistence tests confirm 2 transactions/commits and identical resulting DB/raw content. Measure next production `persistence_commit_calls` and `persistence_sqlite_commit_seconds` before deeper transaction changes.


## TypeSafe diagnostic pilot — 2026-09-21

A project-local `typesafe-ai` skill is installed and locked in `skills-lock.json`. The integration is intentionally outside the crawler hot path: `oursteps/typesafe_diagnostics.py` and `scripts/typesafe_diagnostic.py` read existing aggregate telemetry and optionally obtain four Noul probabilities from TypeSafe. The result is advisory only and cannot mutate archive state.

Preserve this boundary. Do not add TypeSafe calls per fetched page, do not let model output override deterministic parser/auth/robots/backoff rules, and do not send article bodies or credentials. Use TypeSafe first for ambiguous offline diagnosis; only consider a deterministic-parser fallback after measured representative-case accuracy justifies it.

The live API smoke test must be launched locally through `scripts/with_typesafe_keychain.sh`; the remote execution safety layer correctly blocks chaining a Keychain secret into an outbound API call. Unit tests use a fake endpoint and do not require secrets.


### TypeSafe V1.1 evidence

Live smoke test confirmed the API integration works. V1.1 adds recent-run baseline context and separates cumulative historical failures from current evidence. It also adds a deterministic `hold_tuning` gate for current errors/timeouts, preview pending, or incomplete batches. Keep this gate authoritative over TypeSafe output.


## 20-thread production status — 2026-09-21 13:00

Recent 20-thread results are 19/20, 18/20, 20/20, 19/20. The 13:00 run had 2 network errors, 120.755 s adaptive pacing and 1 preview-pending TID, although publish remained healthy at 6,046 full / 3,924 recent and the post-publish check passed. Hold all further throughput tuning; keep 20 as the observation cap and diagnose only if the next runs remain non-clean.


## Mount-resilient Mac LaunchAgents — 2026-09-23

All five production OurSteps LaunchAgents now call a stable local bootstrap at `~/Library/Application Support/OurSteps/launch_project.sh` instead of embedding the NAS SMB mount path. The source template is `scripts/mac_launch_project.sh`; `scripts/install_mac_launchagents.py --install` copies it locally and installs worker-status, control-actions, incremental-sync, Guide Discovery and Historical Backfill with the current production schedules.

The bootstrap accepts `/Volumes/Newhome` and macOS-numbered variants (`Newhome-1`, etc.). If the share is absent, it makes one bounded Keychain-backed macOS remount attempt for `smb://DS923SOPAC.local/Newhome` under a local remount lock, then rescans. Do not put SMB credentials in code or plist files and do not revert LaunchAgents to a NAS-hosted executable path.

Incident evidence: a DS923 restart caused the SMB mount to disappear and briefly use `Newhome-1`; old hard-coded LaunchAgents returned `EX_CONFIG`, worker-status became stale/UNKNOWN, and one Incremental Sync was claimed but interrupted. That job was retained as failed and a normal queued retry later succeeded. Preserve this fail-honest behavior.
