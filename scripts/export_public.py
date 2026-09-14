#!/usr/bin/env python3

"""
Build an isolated static staging export.

NO network access.
NO SQLite access.
NO raw archive access.
NO secrets/session access.

Only generated preview HTML/CSS/JS files are copied.
"""

from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / ".deps")]
from oursteps.deployment import deployment

SOURCE = ROOT / "data" / "preview"
DEST = ROOT / "public-export"
PREVIOUS = ROOT / "public-export.previous"

ARTICLE_RE = re.compile(r"^[0-9]+\.html$")

ALLOWED_FIXED = {
    "index.html",
    "style.css",
    "search.js",
    "reader.js",
    "search-index.json",
}

PRIVATE_MARKERS = [
    b".secrets",
    b"session.json",
    b"archive.sqlite3",
    b"/data/raw",
] + list(dict.fromkeys(
    path.encode("utf-8") for path in (str(ROOT), deployment()[1]) if path
))


def allowed(name):
    return (
        name in ALLOWED_FIXED
        or ARTICLE_RE.fullmatch(name)
    )


if not SOURCE.is_dir():
    raise SystemExit(
        "Preview directory missing: %s" % SOURCE
    )

if not (SOURCE / "index.html").is_file():
    raise SystemExit(
        "Preview index.html missing"
    )


if not (SOURCE / "search-index.json").is_file():
    raise SystemExit(
        "Preview search-index.json missing"
    )


# ---------------------------------------------------------
# Select only explicitly allowed static files.
# ---------------------------------------------------------

selected = []

for path in SOURCE.iterdir():

    if path.is_symlink():
        raise SystemExit(
            "Refusing preview symlink: %s" % path
        )

    if path.is_file() and allowed(path.name):
        selected.append(path)


articles = [
    p for p in selected
    if ARTICLE_RE.fullmatch(p.name)
]

if not articles:
    raise SystemExit(
        "No article HTML files found"
    )


# ---------------------------------------------------------
# Build into temporary sibling directory.
# ---------------------------------------------------------

tmp = Path(
    tempfile.mkdtemp(
        prefix=".public-export.tmp-",
        dir=str(ROOT),
    )
)

try:

    for source in selected:

        target = tmp / source.name

        shutil.copy2(
            source,
            target,
        )


    # Prevent accidental search-engine indexing while
    # this is still only the staging/public-readiness build.
    (tmp / "robots.txt").write_text(
        "User-agent: *\n"
        "Disallow: /\n",
        encoding="utf-8",
    )


    # -----------------------------------------------------
    # Safety scan.
    # -----------------------------------------------------

    problems = []

    for path in tmp.iterdir():

        if path.is_symlink():
            problems.append(
                "%s: symlink" % path.name
            )
            continue

        if not path.is_file():
            problems.append(
                "%s: unexpected non-file" % path.name
            )
            continue

        if path.name == "robots.txt":
            continue

        if not allowed(path.name):
            problems.append(
                "%s: unexpected file" % path.name
            )
            continue

        data = path.read_bytes()

        for marker in PRIVATE_MARKERS:

            if marker in data:
                problems.append(
                    "%s contains private marker %s"
                    % (
                        path.name,
                        marker.decode(
                            "utf-8",
                            errors="replace",
                        ),
                    )
                )


    if problems:

        print(
            "PUBLIC EXPORT SAFETY CHECK FAILED"
        )

        for problem in problems[:50]:
            print(" -", problem)

        raise SystemExit(2)


    # -----------------------------------------------------
    # Public static-file permissions.
    #
    # The temporary directory is atomically renamed to
    # public-export, so normalize its permissions before
    # promotion. nginx runs as a different uid and needs
    # read/execute access to these PUBLIC files only.
    # -----------------------------------------------------

    tmp.chmod(0o755)

    for path in tmp.iterdir():
        if path.is_file():
            path.chmod(0o644)


    # -----------------------------------------------------
    # Build manifest for local audit.
    # Manifest stays OUTSIDE the public directory.
    # -----------------------------------------------------

    hashes = {}

    total_bytes = 0

    for path in sorted(tmp.iterdir()):

        if not path.is_file():
            continue

        data = path.read_bytes()

        total_bytes += len(data)

        hashes[path.name] = (
            hashlib.sha256(data).hexdigest()
        )


    report = {
        "built_at": time.time(),
        "source": str(SOURCE),
        "destination": str(DEST),
        "article_html": len(articles),
        "total_html": len(articles) + 1,
        "static_assets": len(
            [
                p for p in selected
                if p.name in {
                    "style.css",
                    "search.js",
                    "reader.js",
                    "search-index.json",
                }
            ]
        ),
        "total_files": len(
            [
                p for p in tmp.iterdir()
                if p.is_file()
            ]
        ),
        "total_bytes": total_bytes,
        "robots_noindex": True,
        "safety_problems": 0,
        "hashes": hashes,
    }


    # -----------------------------------------------------
    # Promote only after all checks pass.
    # Keep one previous generated export.
    # -----------------------------------------------------

    if PREVIOUS.exists():
        shutil.rmtree(PREVIOUS)

    if DEST.exists():
        DEST.rename(PREVIOUS)

    tmp.rename(DEST)


    report_path = (
        ROOT /
        "data" /
        "public-export-report.json"
    )

    report_path.write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


    print("===== PUBLIC EXPORT RESULT =====")
    print("Article HTML :", report["article_html"])
    print("Total HTML   :", report["total_html"])
    print("Static assets:", report["static_assets"])
    print("Total files  :", report["total_files"])
    print(
        "Size         : %.1f MB"
        % (
            report["total_bytes"]
            / 1024
            / 1024
        )
    )
    print("Safety issues:", 0)
    print("robots.txt   : Disallow /")
    print("Destination  :", DEST)
    print()
    print(
        "RESULT: PASS - isolated staging export created."
    )


finally:

    if tmp.exists():
        shutil.rmtree(tmp)
