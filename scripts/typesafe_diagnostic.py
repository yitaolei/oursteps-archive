#!/usr/bin/env python3
"""Read existing telemetry and optionally ask TypeSafe for non-authoritative diagnostics."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from oursteps.typesafe_diagnostics import TypeSafeDiagnostics


def load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def build_state(root: Path) -> dict:
    perf = load(root / "data/backfill-performance.json")
    inv = load(root / "data/inventory-report.json")
    status = load(root / "data/run-status.json")
    p = perf.get("performance") or {}
    failures = status.get("failures") or []
    gaps = status.get("gaps") or []

    def counts(rows, key):
        out = {}
        for row in rows:
            value = str((row or {}).get(key) or "unknown")
            out[value] = out.get(value, 0) + 1
        return out

    return {
        "purpose": "read-only OurSteps production diagnostic; never an execution instruction",
        "latest_backfill": {
            "status": perf.get("status"),
            "max_threads": perf.get("max_threads"),
            "started_threads": perf.get("started_threads"),
            "completed_this_run": perf.get("completed_this_run"),
            "crawl_elapsed_seconds": perf.get("crawl_elapsed_seconds"),
            "elapsed_seconds": perf.get("elapsed_seconds"),
            "network_errors": p.get("network_errors"),
            "network_timeouts": p.get("network_timeouts"),
            "backoff_seconds": p.get("backoff_seconds"),
            "pacing_adaptive_seconds": p.get("pacing_adaptive_seconds"),
            "preview_pending_tids": perf.get("preview_pending_tids"),
        },
        "inventory": {
            "total_discovered": inv.get("total_discovered"),
            "fully_archived": inv.get("fully_archived"),
            "remaining": inv.get("remaining"),
            "failed_or_retry_pending": inv.get("failed_or_retry_pending"),
            "archive_scope": inv.get("archive_scope"),
        },
        "reported_failure_state_counts": counts(failures, "state"),
        "reported_failure_error_counts": counts(failures, "error"),
        "reported_gap_reason_counts": counts(gaps, "reason"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    state = build_state(ROOT)
    result = {
        "authoritative": False,
        "may_change_crawler_behavior": False,
        "state": state,
        "typesafe": TypeSafeDiagnostics().evaluate(state),
    }
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(text)
    print(text, end="")


if __name__ == "__main__":
    main()
