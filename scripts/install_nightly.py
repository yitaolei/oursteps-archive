#!/usr/bin/env python3
"""Compatibility installer for the current incremental-sync LaunchAgent."""
from install_mac_launchagents import install

if __name__ == "__main__":
    install(["local.oursteps.incremental-sync"])
