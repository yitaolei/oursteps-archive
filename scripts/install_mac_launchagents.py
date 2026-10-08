#!/usr/bin/env python3
"""Install the current production OurSteps macOS LaunchAgents safely."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
LABEL_PREFIX = "local.oursteps."
BOOTSTRAP_SOURCE = ROOT / "scripts/mac_launch_project.sh"
BOOTSTRAP_TARGET = Path.home() / "Library/Application Support/OurSteps/launch_project.sh"
CONTROL_TARGET = Path.home() / "Library/Application Support/OurSteps/control-plane"
CONTROL_LABELS = {
    "local.oursteps.worker-status",
    "local.oursteps.control-actions",
    "local.oursteps.incremental-sync",
    "local.oursteps.guide-discovery",
    "local.oursteps.historical-backfill",
}
LAUNCH_DIR = Path.home() / "Library/LaunchAgents"
LOG_DIR = Path.home() / "Library/Logs"


def calendar(hours, minute=0):
    return [{"Hour": hour, "Minute": minute} for hour in hours]


def specs():
    wrapper = str(BOOTSTRAP_TARGET)
    home = str(Path.home())
    return {
        "local.oursteps.keep-awake": {
            # Mac Studio is the archive scheduler host. Keep system sleep disabled
            # while on AC without changing global pmset settings; display sleep is
            # unaffected. This is reversible by unloading this user LaunchAgent.
            "ProgramArguments": ["/usr/bin/caffeinate", "-s"],
            "RunAtLoad": True,
            "KeepAlive": True,
            "StandardOutPath": str(LOG_DIR / "oursteps-keep-awake.log"),
            "StandardErrorPath": str(LOG_DIR / "oursteps-keep-awake-error.log"),
            "WorkingDirectory": home,
            "ProcessType": "Background",
            "ThrottleInterval": 30,
        },
        "local.oursteps.worker-status": {
            "ProgramArguments": [wrapper, "local-clean-nas-env", "scripts/worker_status_snapshot.py"],
            "StartInterval": 30,
            "RunAtLoad": True,
            "StandardOutPath": str(LOG_DIR / "oursteps-worker-status.log"),
            "StandardErrorPath": str(LOG_DIR / "oursteps-worker-status-error.log"),
            "WorkingDirectory": home,
            "ProcessType": "Background",
        },
        "local.oursteps.control-actions": {
            "ProgramArguments": [wrapper, "local-clean-nas-env", "scripts/control_action_runner.py"],
            "StartInterval": 60,
            "RunAtLoad": False,
            "StandardOutPath": str(LOG_DIR / "oursteps-control-actions.log"),
            "StandardErrorPath": str(LOG_DIR / "oursteps-control-actions-error.log"),
            "WorkingDirectory": home,
            "ProcessType": "Background",
        },
        "local.oursteps.incremental-sync": {
            "ProgramArguments": [wrapper, "local-clean-nas-env", "scripts/update_now.py", "--now"],
            "StartCalendarInterval": calendar(range(6, 23, 2)),
            "RunAtLoad": False,
            "StandardOutPath": str(LOG_DIR / "oursteps-update-launchd.log"),
            "StandardErrorPath": str(LOG_DIR / "oursteps-update-launchd-error.log"),
            "WorkingDirectory": home,
            "ProcessType": "Background",
        },
        "local.oursteps.guide-discovery": {
            "ProgramArguments": [wrapper, "local-clean-nas-env", "scripts/history_launcher.py",
                                 "guide-discover", "--max-pages", "25", "--max-minutes", "20"],
            "StartCalendarInterval": {"Hour": 3, "Minute": 15},
            "RunAtLoad": False,
            "StandardOutPath": str(LOG_DIR / "oursteps-guide-discovery.log"),
            "StandardErrorPath": str(LOG_DIR / "oursteps-guide-discovery-error.log"),
            "WorkingDirectory": home,
            "ProcessType": "Background",
            "Nice": 10,
        },
        "local.oursteps.historical-backfill": {
            "ProgramArguments": [wrapper, "local-clean-nas-env", "scripts/history_launcher.py",
                                 "backfill", "--best-effort", "--now",
                                 "--max-threads", "120", "--max-minutes", "45"],
            "StartCalendarInterval": calendar(range(7, 22, 2)),
            "RunAtLoad": False,
            "StandardOutPath": str(LOG_DIR / "oursteps-history-backfill.log"),
            "StandardErrorPath": str(LOG_DIR / "oursteps-history-backfill-error.log"),
            "WorkingDirectory": home,
            "ProcessType": "Background",
            "Nice": 10,
        },
    }


def sync_control_plane():
    """Stage a small Mac controller bundle; live SQLite/data remain NAS-only."""
    parent = CONTROL_TARGET.parent
    parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".control-plane-", dir=parent))
    old = CONTROL_TARGET.with_name("control-plane.previous")
    try:
        for name in ("archive.py", "requirements-auth.txt", "config.local.json"):
            source = ROOT / name
            if not source.is_file():
                raise FileNotFoundError(source)
            shutil.copy2(source, stage / name)
        for name in ("oursteps", "scripts", ".deps", ".secrets"):
            source = ROOT / name
            if not source.is_dir():
                raise FileNotFoundError(source)
            shutil.copytree(source, stage / name)
        stage.chmod(0o700)
        (stage / "config.local.json").chmod(0o600)
        (stage / ".secrets").chmod(0o700)
        for item in (stage / ".secrets").iterdir():
            if item.is_file():
                item.chmod(0o600)
        if old.exists():
            shutil.rmtree(old)
        if CONTROL_TARGET.exists():
            CONTROL_TARGET.rename(old)
        stage.rename(CONTROL_TARGET)
        if old.exists():
            shutil.rmtree(old)
    except Exception:
        if stage.exists():
            shutil.rmtree(stage)
        if not CONTROL_TARGET.exists() and old.exists():
            old.rename(CONTROL_TARGET)
        raise
    print(f"synced control plane to {CONTROL_TARGET}")


def install(labels=None):
    if sys.platform != "darwin":
        raise SystemExit("Run on the Mac that owns the OurSteps schedules.")
    labels = labels or list(specs())
    if CONTROL_LABELS.intersection(labels):
        sync_control_plane()
    BOOTSTRAP_TARGET.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(BOOTSTRAP_SOURCE, BOOTSTRAP_TARGET)
    BOOTSTRAP_TARGET.chmod(0o700)
    LAUNCH_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    domain = f"gui/{os.getuid()}"
    all_specs = specs()
    for label in labels:
        config = {"Label": label, **all_specs[label]}
        target = LAUNCH_DIR / f"{label}.plist"
        target.write_bytes(plistlib.dumps(config, sort_keys=False))
        target.chmod(0o600)
        subprocess.run(["launchctl", "bootout", f"{domain}/{label}"], capture_output=True)
        subprocess.run(["launchctl", "bootstrap", domain, str(target)], check=True)
        print(f"installed {label}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--install", action="store_true",
                        help="Write bootstrap/plists and reload all production LaunchAgents.")
    parser.add_argument("--label", action="append", choices=sorted(specs()),
                        help="Install only this label; may be repeated.")
    args = parser.parse_args()
    if not args.install:
        print("Dry-run: production LaunchAgent labels:")
        for label, spec in specs().items():
            print(label, spec["ProgramArguments"])
        return 0
    install(args.label)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
