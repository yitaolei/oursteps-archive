# Historical Backfill Throughput V2

## Goal

Increase successful historical articles per day without weakening robots, request pacing, retry/backoff, authentication checks, worker locks, publication safety, or rollback guarantees.

## Baseline findings — 2026-09-19

Current known inventory snapshot:
- discovered: 7,377
- fully archived: 5,669
- remaining: 1,708

Historical completed-thread page distribution:
- average parsed pages/thread: 1.99
- median: 1
- 56.64% are one page
- 81.43% are two pages or fewer
- 90.84% are three pages or fewer
- 98.92% are ten pages or fewer

Recent real crawl spans for 10 historical TIDs:
- 2026-09-18 19:00: about 91.1 s
- 2026-09-18 21:00: about 97.7 s
- 2026-09-19 07:00: about 84.7 s

This proves the network crawl itself is not taking 20–30 minutes for 10 TIDs.

The 2026-09-19 09:00 15-thread baseline completed its crawl in **243.93 s**. SQLite snapshot timestamps show **24 network snapshots** after 09:00, including **22 thread-page snapshots across 15 TIDs**. That is about **10.16 s per network fetch**, essentially the configured 10-second request-spacing floor. This is strong evidence that network concurrency is not the current first-order optimization target.

The 15 TIDs required 22 fetched thread pages in this run (about 1.47 fetched pages/TID); the whole completed archive averages 1.99 parsed pages/thread. Future capacity planning should use request budget, not thread count alone.

Current fetch policy:
- one shared Fetcher
- single-domain global pacing
- minimum effective request spacing: 10 s
- adaptive delay currently below the 10 s floor
- retry/backoff and pause safeguards remain active

Stored robots policy currently includes `Request-rate: 1/5`, `Crawl-delay: 5`, and `Visit-time: 1400-2200` (interpreted by the crawler in UTC). The current 10-second request floor is more conservative than the explicit 5-second rate/delay, but scheduled historical backfill currently passes `--now`, which bypasses the visit-time gate. Do not increase daily crawl duty cycle until scheduled historical work is moved back inside the permitted visit-time window and the bypass is removed for scheduled runs.

## Primary bottleneck found

history.backfill() currently calls preview.build(store) after every historical batch.

That full builder:
- iterates every complete thread;
- re-queries posts/metrics for every article;
- rewrites every article HTML file;
- performs an atomic write + fsync per file;
- rebuilds index and search index.

At 5,700+ completed articles this is O(total archive), not O(changed batch).

Filesystem evidence from the 2026-09-19 07:00 run shows the full preview rebuild rewrote 5,714 article HTML files from 07:02:17 to 07:24:26: **1,329.7 seconds (22m 09.7s)**.

An isolated benchmark using the existing strict incremental renderer:
- 10 TIDs
- temporary staging directory
- no live preview changes
- elapsed: **9.91 s**

This makes incremental preview the highest-value Phase A optimization.

## Phase A — Incremental historical preview

Replace the historical full preview.build(store) with the existing staged preview_batch.build_batch(store, tids=completed_tids) path.

Requirements:
- persist each attempted historical TID in NAS SQLite before its crawl starts;
- keep partial/retry TIDs pending but render only those whose DB status is complete;
- remove a TID from durable pending only after incremental preview succeeds;
- reuse preview_batch's commit-pending marker for interrupted file replacement;
- public publish remains fail-closed while preview-incremental-pending exists;
- a non-zero historical worker exit prevents the launcher from publishing;
- no automatic fallback to a full rebuild;
- full rebuild remains an explicit maintenance/recovery operation.

Do not change request pacing or concurrency in this phase.

Recovery invariant: a crash after a thread becomes complete but before preview rendering must still leave a durable TID that the next backfill can render without re-crawling the thread.

## Phase B — Measure public release cost

After Phase A, separately measure:
- preview incremental seconds;
- public release staging seconds;
- full release validation seconds;
- recent-1y staging seconds;
- healthcheck seconds.

Observed 07:00 timing also suggests publish/validation is roughly one additional minute after the 22-minute full preview rebuild. The publisher already hard-links unchanged files from the previous immutable release, which is good. However it still hashes and validates the full article set. Optimize this only if measurements show it becomes the next dominant cost.

Potential future direction:
- trusted previous manifest + delta manifest;
- hard-link unchanged article files;
- hash only changed/new files plus fixed shared assets;
- periodic full validation as a safety sweep.

Do not weaken current release integrity checks without a replacement invariant.

## Phase C — Robots-aligned schedule + dynamic work budget

First move scheduled historical backfill into the robots `Visit-time` window and remove the scheduled `--now` bypass. Keep the existing 10-second global request floor unless a separate policy review deliberately changes it.

Once post-processing is cheap and the schedule is robots-aligned, stop treating max_threads as the main throughput control.

Prefer:
- fixed maximum wall-clock crawl budget;
- existing global per-domain pacing;
- adaptive batch/thread cap based on recent success;
- automatic decrease after 429/5xx/Busy/timeouts;
- gradual increase after several clean runs.

The time budget should remain the hard stop.

Capacity planning only (not a production target yet): at the unchanged 10-second request floor, the ceiling is 360 requests/hour. Using the archive-wide 1.99 parsed pages/thread average gives roughly 181 threads/hour, or about 135 threads in a 45-minute request budget before overhead. Six robots-window 45-minute historical runs per day, leaving room for the existing 03:15 Guide V2 and 06:00 Incremental Sync worker, would be on the order of 800 historical threads/day. Validate with production telemetry before adopting any such cap or cadence.

## Phase D — Concurrency only if measurements justify it

Do not add parallel workers just because a crawler can be asynchronous.

For a single domain with a 10 s request-spacing floor, concurrency only helps materially when response latency regularly exceeds that spacing. If request starts must remain 10 s apart, extra workers cannot multiply the allowed request rate.

If later metrics show long network waits, test at most limited in-flight concurrency with one shared domain rate limiter and unchanged request-start pacing.

## Decision gates

Phase A can proceed only after:
1. 09:00+ telemetry confirms crawl vs full-preview timing;
2. incremental staging benchmark remains successful;
3. tests cover newly-completed TID selection, partial-thread exclusion, and failed-stage safety.

Further batch/concurrency tuning waits until Phase A production data is available.


## Phase A production telemetry — 2026-09-19 09:00

The instrumented 15-thread run completed all 15 TIDs with 23 network fetches (22 thread-page fetches), or 1.533 requests per completed thread. Crawl time was 244.707 s; network I/O 18.291 s; throttle 98.796 s; parse 37.540 s; measured persistence 23.376 s. The full preview rebuild then took 1,473.144 s, about 85.7% of the historical-worker wall time.

A real incremental preview run for 15 complete TIDs through `archive.py build-preview --tids ...` took 11.953 s renderer time and 12.80 s wall time with `full_rebuild=false`. This confirms staged incremental preview is the first-order throughput optimization.

Phase A implementation uses the existing NAS SQLite `settings` table as a durable pending set under `historical_preview_pending_tids`. Each eligible historical TID is persisted before its crawl starts. Only pending TIDs that become `complete` are sent to `preview_batch.build_batch()`; successful renders clear those TIDs, while partial/retry TIDs remain pending across restarts. Incremental-stage failure is fail-closed and does not silently trigger a full rebuild.

## Phase B pacing diagnostics — 2026-09-19 17:00 production run

The 17:00 production run recovered substantially from the earlier Busy-heavy anomaly: 13/15 threads completed in 687.772 s, with 18 network fetches, 2 network errors, 7.705 s network time, 27.960 s parse time, 17.249 s persistence time, and a 16.772 s incremental preview. Requests per completed thread returned to 1.385.

The dominant measured component was 585.995 s in `throttle_seconds`. Post-run state showed `adaptive_delay=27.91s` while `pause_until` was already expired. This is strong evidence that most of the 17:00 wait was adaptive request pacing rather than active global Busy backoff, but the existing metric could not prove the split directly.

Phase B therefore adds telemetry only: `pacing_base_seconds`, `pacing_adaptive_seconds`, and `backoff_seconds`, while retaining the existing aggregate `throttle_seconds`. No request rate, concurrency, robots policy, retry/backoff rule, adaptive-delay calculation, worker lock, or publication behavior changes in this checkpoint. Use subsequent clean production runs to determine whether adaptive pacing is the next structural bottleneck before considering any algorithm change.

## Phase A close-out and 19:00 clean production confirmation

Phase A is complete. The 19:00 production run completed 15/15 threads in 143.893 s crawl / 165.475 s total worker time, with 22 network fetches, zero network errors/timeouts, 1.467 requests per completed thread, 21.284 s incremental preview, and zero preview-pending TIDs. The two TIDs left pending by the earlier Busy-heavy run were recovered automatically without a manual full rebuild.

The new pacing split showed 24.008 s aggregate throttle: 0.542 s base pacing, 23.466 s adaptive pacing, and 0 s global backoff. Network time was 2.953 s, parse 37.983 s, persistence 22.202 s, and SQLite commit time 19.483 s across 64 commits. This clean run confirms adaptive pacing is not a structural bottleneck when the upstream site is healthy.

## Phase B public-release telemetry

Phase B now instruments the remaining publish path without changing release semantics. `publish()` reports previous-release validation, preparation/source hashing, full-release staging, full validation, recent-1y staging, final validation, finalize/switch, and total publish seconds. `public_healthcheck.py` reports `HEALTHCHECK_SECONDS`. These timing fields are excluded from release manifests and release identity, so deterministic releases remain unchanged.

Phase B is not closed until at least one changed production release records these timings. If publish/healthcheck remain small relative to crawl, mark Phase B complete and proceed to Phase C; do not optimize release validation merely because it is O(total archive) unless production timing justifies it.

A production read-only measurement on 5,811 current articles showed the pre-simplification public healthcheck took 66.536 s. An idempotent unchanged publish took 24.904 s, including 19.070 s validating the previous/current release state and 5.832 s preparation/source hashing. Inspection found the healthcheck fully validated the current release twice: once directly against DB expectations and again through `validate_saved()`.

The low-risk Phase B simplification removes only that duplicate current scan. `validate_saved(current)` still performs the complete release/hash/manifest validation; the healthcheck then explicitly compares its article TIDs and recent policy with current NAS SQLite expectations, and still fully validates `previous` for rollback safety. Production re-measurement passed and reduced healthcheck time to 37.741 s, a 43% reduction, without weakening current/previous checksum, DB parity, recent-scope, privacy, or rollback invariants.


## Phase C adaptive recovery — 2026-09-20

07:00 production completed 15/15 with zero network errors/timeouts, but still spent 57.886 s in adaptive pacing while `error_streak=0`. Inspection showed that a transient-error penalty persisted in SQLite and successful responses only retained 90% of the prior adaptive delay each time, so clean runs could remain slowed by yesterday's network conditions.

The recovery rule is now bounded and health-sensitive: successful HTTP 200 responses with `error_streak=0` retain 80% of the previous adaptive delay; responses while recovering from an error streak, and non-200 responses, retain the previous 90% decay behavior. Existing robots/base pacing remains the hard floor, transient errors still double adaptive delay, pause/backoff rules are unchanged, and no concurrency is added.

Focused tests cover healthy vs recovering decay, latency floor, and the 120 s cap.

## Phase C persistence simplification — 2026-09-20

Latest clean production after health-sensitive adaptive recovery completed 15/15 with zero network errors/timeouts. Adaptive pacing was 49.608 s versus the 07:00 baseline 57.886 s (-14.3%). Persistence was 27.627 s, of which SQLite commit time was 24.465 s across 76 commit calls (88.6% of measured persistence). SQL execution itself was only 0.147 s.

Inspection showed a simple avoidable source of durable writes: successful pages repeatedly call `set_setting('error_streak', 0)` even when the persisted value is already `0`. On NAS SQLite with `journal_mode=DELETE` and `synchronous=FULL`, that no-op write still creates a transaction and durable commit.

`Store.set_setting()` now skips the write entirely when the stored string value already equals the requested value. Missing keys are still created, changed values are still committed immediately, and all existing durable pacing/auth/backoff settings keep their semantics. This is intentionally narrower than transaction batching: no transaction boundaries are merged, no fsync policy is weakened, and no crawler concurrency/pacing behavior changes.

For a typical clean 15-thread batch with roughly 25 successful page parses, this should remove about 25 unnecessary SQLite commits. Measure the next production run before attempting deeper transaction coalescing. The next structural candidate remains duplicate full-release validation across publish and the immediate healthcheck.

## Phase C publish/healthcheck deduplication — 2026-09-20

A changed production publish currently costs about 71.8 s and the immediately chained full `public_healthcheck.py` costs another 37.7 s. The publisher has already fully validated the prior release, the staged full release, and the final full+recent release before atomically switching `current`; immediately rescanning all current/previous article files therefore duplicates work inside the same backfill pipeline.

Historical Backfill no longer chains a second full healthcheck immediately after a successful publish. `publish_public.py` now performs a narrow `post_publish_check()` in the same process: verify the live `current` pointer equals the just-published release, `previous` exists, release metadata is present, the manifest digest matches the release identity, and article/recent counts match the publisher result. Any failure still makes publication return non-zero.

The standalone `public_healthcheck.py` is unchanged and remains the independent full filesystem/checksum/DB/rollback integrity sweep for manual or separately scheduled health checks. This change removes duplicate immediate scanning; it does not weaken the publisher's pre-switch validation or deterministic release construction.

## Phase C final publish validation reuse — 2026-09-20

The changed-release path previously scanned the entire staged top-level release twice: once before `stage_recent()`, then again during final validation with the new `recent-1y/` scope. `stage_recent()` only creates/modifies the `recent-1y/` subtree, so the second top-level scan was redundant.

The publisher now keeps the first full validation report/hashes, stages `recent-1y/`, validates only that new subtree, verifies every shared recent article/static hash against the already validated full hashes, verifies the recent search index is exactly the allowed subset of the validated full search index, then merges recent hashes into the final manifest. Deterministic release identity still includes both full and recent hashes.

This preserves the pre-switch full-release integrity check and recent/full parity while avoiding a second read/hash pass over all top-level articles. Focused tests cover recent tamper detection, idempotency/rollback, and scope/rolling-hash behavior.

## Phase C changed-publish previous-release prescan removal — 2026-09-20

Changed publishes previously spent about 18–19 s fully validating the current release before staging, mainly to make hardlink reuse trustworthy. That full prescan is unnecessary when the new stage itself is fully validated against the authoritative current preview/source hashes before any live pointer switch.

Publish now reads the prior release metadata only for hardlink candidate selection. For a genuinely changed publish, it does not pre-scan every old article. After staging, the one full top-level validation must produce hashes exactly equal to `source_hashes`; therefore any stale/tampered old hardlink source fails closed before `current` can switch. A focused test tampers an old shared `style.css`, forces a changed publish, and confirms publication is refused by the staged-source parity check.

The unchanged fast path remains conservative: when source hashes/recent policy exactly match the old manifest, `validate_saved(current)` still runs before returning `unchanged`. Rollback also retains full validation of current/previous. This optimization therefore targets changed historical publishes only and should remove roughly the prior `previous_validation_seconds` cost without weakening source parity.
