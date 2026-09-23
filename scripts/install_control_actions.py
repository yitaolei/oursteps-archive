#!/usr/bin/env python3
"""Compatibility installer for Control Center LaunchAgents."""
from install_mac_launchagents import install

if __name__ == "__main__":
    install([
        "local.oursteps.control-actions",
        "local.oursteps.worker-status",
    ])
