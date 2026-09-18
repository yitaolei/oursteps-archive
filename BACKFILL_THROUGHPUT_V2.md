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
