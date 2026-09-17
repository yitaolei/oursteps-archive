# OurSteps Archive

A self-hosted archive for a user's own posts and threads on the OurSteps forum.

The project discovers, verifies, stores, and renders posts belonging to a configured forum account while keeping credentials, raw archive data, and private deployment details outside the public repository.

## Features

- Archive threads created by a configured OurSteps account.
- Preserve the configured author's posts across multi-page threads.
- Verify archive ownership by configured UID.
- Resume interrupted discovery and historical reconciliation.
- Store archive metadata in SQLite.
- Preserve raw source snapshots separately from generated output.
- Generate a compact local HTML preview.
- Produce isolated static public releases.
- Support rolling recent-content and full-archive access scopes.
- Use conservative retry/backoff behavior for site errors and Busy responses.
- Detect requested-page versus paginator-page mismatches.
- Support macOS authentication with a dedicated browser session.
- Support NAS-oriented production deployment.

## Safety and privacy

Do not commit or publish:

- passwords, cookies, or session material
- `.secrets/`
- `config.local.json`
- archive databases
- raw downloaded pages
- generated preview/public archives
- machine-specific deployment details

The supplied `.gitignore` excludes local configuration, secrets, runtime data, and generated output.

## Requirements

The core project uses Python 3.

Dependencies are listed in:

- `requirements.txt`
- `requirements-auth.txt`

For SQLite reliability, production database reads and writes should occur on the filesystem native to the machine running SQLite. Avoid concurrent SQLite writes through SMB or other network-mounted paths.

## Configuration

Copy the example configuration:

    cp config.example.json config.local.json

Edit `config.local.json` with your own values:

    {
      "uid": 12345,
      "username": "example_user",
      "display_name": "Example Author",
      "deployment": {
        "nas_host": "archive-user@nas.example.invalid",
        "nas_project": "/srv/oursteps-archive"
      },
      "public_access": {
        "recent_user": "recent_reader",
        "full_user": "archive_owner"
      }
    }

`config.local.json` is private and ignored by Git.

You can point the application at another private config file with `OURSTEPS_CONFIG`.

Deployment overrides are also supported with `OURSTEPS_NAS_HOST` and `OURSTEPS_NAS_PROJECT`. Legacy aliases `OURSTEPS_NAS` and `OURSTEPS_NAS_ROOT` remain supported.

There are no built-in personal UID, username, NAS host, or local-path defaults. Missing or invalid required configuration fails explicitly.

## Authentication

Authentication is separated from ordinary browser sessions.

On macOS, the included workflow can use a dedicated browser session and save authorized session state under the ignored `.secrets/` directory. The application verifies that the authenticated account matches the configured UID and username.

The macOS launcher is:

    Authenticate OurSteps.command

## Daily sync

Run an immediate update with:

    python3 scripts/update_now.py --now

The updater verifies authentication, discovers current threads for the configured account, archives new material, updates metadata, and runs the configured publishing workflow.

For unattended operation, schedule the updater using the operating system scheduler appropriate to your installation.

<!-- historical-backfill-auto-publish-v1 -->
### Historical backfill publication

A successful historical backfill launched through `scripts/history_launcher.py` now completes the publication loop automatically: the existing preview rebuild finishes first, then the launcher runs the public publisher and public healthcheck. This keeps newly completed historical threads visible in the published archive without waiting for a later daily sync.


## Local preview

Start the generated-preview server with:

    python3 preview.py

The preview server is intended for local access and serves generated files rather than exposing the SQLite database or raw archive directly.

## Historical archive tools

### Worker status / manual collision protection

Before manual archive/authentication launchers start, they first check the NAS archive worker lock. If another archive writer is active, the manual action exits before authentication or sync work begins. Use `OurSteps Worker Status.command` for a quick one-shot `RUNNING` / `IDLE` check, or `Historical Backfill Live Status.command` for the read-only progress monitor.


The repository includes resumable tools for historical discovery, status checking, reconciliation, and controlled backfill.

macOS convenience launchers include:

- `Discover All OurSteps Threads.command`
- `Discover Remaining OurSteps Threads.command`
- `Historical Archive Status.command`
- `Historical Backfill.command`
- `Historical Backfill Live Status.command`

Completed work should be reused rather than rescanned unnecessarily. Ambiguous or incomplete material is preserved as incomplete/review-required instead of being forced to complete.

## Parser safety

The parser validates archive ownership and pagination.

When a page exposes an explicit current-page marker, it must agree with the requested page. A mismatch is treated as a parse error rather than silently importing the wrong page.

## Static public publishing

Public output is generated separately from private archive data.

The release pipeline validates allowed files, expected article IDs, permissions, private-data markers, search/index consistency, and restrictive `robots.txt` behavior.

The production nginx configuration is intentionally not committed. The repository instead contains:

    nginx-public-stable.conf.template

After configuring `public_access.recent_user` and `public_access.full_user`, explicitly render a private nginx config with:

    python3 scripts/render_public_nginx.py

This creates the ignored local file `nginx-public-stable.conf`.

Publishing validates the generated nginx configuration; it does not silently regenerate it.

## Public access scopes

The stable nginx configuration supports two authenticated scopes:

- rolling recent archive
- full archive

Actual deployment usernames remain private local configuration. Unknown authenticated users fail closed.

## Repository layout

- `oursteps/` - core archive, parsing, authentication, sync, and preview modules
- `scripts/` - maintenance, publishing, and operational tools
- `tests/` - synthetic fixtures and regression tests
- `archive.py` - archive command entry point
- `preview.py` - local preview server
- `config.example.json` - public configuration example
- `nginx-public-stable.conf.template` - public nginx template
- `compose.yaml` - core container configuration
- `compose.public.yaml` - static public-serving configuration

Private/runtime paths such as `data/`, `.secrets/`, generated releases, and `config.local.json` are intentionally excluded from Git.

## Tests

The test suite uses synthetic identity and deployment data and should not depend on a developer's private `config.local.json`.

Prefer focused tests while iterating and broader validation before a release. Some nginx integration tests require a native nginx executable and may be skipped where nginx is unavailable.

## Current code-assistant handoff

The current production-safe code handoff is [`CODEX_HANDOFF.md`](CODEX_HANDOFF.md). Code assistants should read it before changing crawler, publication, Control Center, analytics, scheduling or role-scope behavior. It records the completed Phase 1-4 close-out, live-validated safety fixes, stable checkpoints, and work that must not be repeated without a demonstrated regression.

## Operations and control-center roadmap

Operator workflows, active scheduler relationships, safety boundaries, and the four-phase close-out roadmap are documented in [`OPERATIONS.md`](OPERATIONS.md). A public-safe machine-readable capability inventory lives in [`config/tool_registry.json`](config/tool_registry.json); it contains no credentials and does not itself grant execution authority. The owner-only 8448 Control Center now also supports fixed allowlisted safe actions through an internal queue and a Mac-side runner; rollback and authentication remain outside the web UI.

The Phase 2 Control Center is implemented as owner-only static release assets (`/control-center.html` plus a sanitized JSON snapshot), preserving the existing read-only nginx architecture and recent/full role separation.

## First-party article read analytics

The public nginx layer records privacy-minimal article page loads in a private NAS log: only timestamp, article path/TID, and HTTP status are retained. IP address, authenticated username, and User-Agent are deliberately excluded. Successful article GETs are aggregated into the generated `article-views.json`; article pages label this separately as `本站阅读`, while the existing `浏览` value continues to mean the source OurSteps forum view count.

## Project status

The project supports ongoing incremental archiving, local preview generation, historical reconciliation, and isolated static publishing.

The public repository is intended to provide reusable software rather than a copy of one user's archive or deployment history.

## Disclaimer

Forum layouts and authentication flows can change. Review crawler behavior and site rules before running automated archival jobs.

Use the software only for content and accounts you are authorized to access.
