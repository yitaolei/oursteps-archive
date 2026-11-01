# OurSteps Archive - Studio Incremental Sync JS Challenge Incident

Date: 2026-11-01 (Australia/Sydney)
Host: Mac Studio (`ylei`), local-authority database only
Affected thread: TID 1905193, Fremantle bridge article

## User impact

The user posted TID 1905193 around 14:37 AEDT and clicked the Studio Control Center **Incremental Sync Now** button. The UI showed `succeeded`, yet the new article was absent from the Studio archive. The user reported and verified the mismatch. Do not treat process exit code 0 or release validation alone as proof that all new threads were archived.

## Root cause

1. The daily listing identified TID 1905193, but fetching page 1 received incomplete JavaScript challenge HTML (`incomplete_html_js_challenge`). The SQLite thread remained `pending`; its page-1 job was `retry_later`.
2. A later scan returned `status: success` while preserving `deferred_threads: [1905193]` and `discovery_complete: false`. The old Studio cycle and manual web worker interpreted exit code 0 as full success, then published a release without the new article.
3. Legacy Playwright/browser fallback scripts write to NAS over SSH, so they must not be used for Studio primary. A Studio-native importer was needed.

## Fix implemented

- `scripts/studio_primary_cycle.py`: detects outstanding `daily-challenge` page-1 deferred jobs and returns exit code 3 (`partial`) after normal local publish; exit 0 means no matching deferred thread remains. The existing shared Studio cycle lock and local-authority checks remain in place. Refreshes both Studio HTTPS sites after publishing.
- `scripts/studio_manual_sync_worker.py`: maps exit 3 to `partial` instead of `succeeded`. On partial, attempts a **single** locally authenticated browser recovery for one deferred page-1 TID, flushes verified preview, then re-runs the bounded Studio local sync/publish cycle. Other failures remain failures; no unlimited retry loop.
- `scripts/studio_browser_recover_one.py`: uses local Chrome/Playwright and existing authenticated session. Validates the response, canonical TID/page and parser output, then writes through the Studio-local `Store` and `worker.lock`; no NAS SSH import.
- `scripts/studio_flush_browser_preview.py`: flushes eligible imported snapshots to Studio incremental preview with the existing worker lock. Never publish challenge HTML.
- Control Center API: local action is started on Studio, with job states `running`, `succeeded`, `partial`, `failed`; owner-only Apache proxy on staging port 18751. No changes to the NAS 8448 route.

## Verified recovery and production result

- Local browser recovery returned `imported` and `thread_status: complete` for TID 1905193.
- Incremental preview successfully rendered 1905193 and `post_publish_check: pass`.
- Studio published 15,162 complete articles, with 4,468 in recent one-year scope (previously 15,161 / 4,467).
- `public-site/current/1905193.html` and `public-site/current/recent-1y/1905193.html` both exist. The user confirmed the article is visible on the Studio website.
- Repeat manual sync after recovery found 17 current-day articles, returned `succeeded` with exit code 0, and passed publish validation.
- Studio SQLite `PRAGMA quick_check` returned `ok`; NAS original database mtime stayed at 2026-11-01 10:07:30 AEDT during these checks.

## Known limitations and follow-up

- Recovery is deliberately limited to one eligible first-page thread per manual request. A new/deferred thread may remain behind backoff or other access challenges, requiring subsequent safe retries.
- The simulated forced-challenge regression branch, failed-browser case, simultaneous-click case and `OStester` account-specific negative authorization test are not yet fully exercised. Do not claim complete fault tolerance.
- A process crash during manual sync may leave a stale `running` receipt. Add a bounded stale-job state check before declaring unattended recovery complete.
- The scheduled hourly sync uses the same Studio cycle but does **not yet** automatically run the browser-fallback wrapper used by the web manual worker. Bring scheduled behavior into alignment after safe validation.
- Confirm real-world `partial` UI status on a future challenge event and inspect post-run logs. Do not bypass rate limits or site denials.
- NAS 8448 remains the original entrypoint and old NAS writers must stay disabled until cutover validation and rollback test are complete.

## Relevant local paths

- Runtime: `~/Library/Application Support/OurSteps/studio-primary-runtime/`
- Manual run log: `data/logs/studio-manual-sync.log`
- Manual job receipt: `data/studio-manual-sync.json`
- Health receipt: `data/studio-health.json`
- Local test Control Center: `https://192.168.0.125:18751/control-center.html`
- Verified article: `https://192.168.0.125:18751/1905193.html`

## Rollback guidance

Do not revert the entire local SQLite or copy NAS DB back. For an application regression, disable the affected manual action first; restore only a vetted script/config checkpoint, validate with `py_compile`, `httpd -t`, SQLite read-only checks and a normal authorized Studio sync. Do not enable old NAS LaunchAgents. Preserve incident logs and the `partial` distinction throughout rollback.
