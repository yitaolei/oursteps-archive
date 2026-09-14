#!/usr/bin/env python3
"""Install a deterministic macOS LaunchAgent. No Codex/ChatGPT automation."""
import os
from pathlib import Path
import plistlib
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]
if sys.platform!='darwin':raise SystemExit('Run on the Mac which owns the nightly schedule.')
label='local.oursteps.incremental-sync'
target=Path.home()/'Library/LaunchAgents'/(label+'.plist')
logs=Path.home()/'Library/Logs/OurSteps';logs.mkdir(parents=True,exist_ok=True)
config=dict(Label=label,ProgramArguments=[sys.executable,str(ROOT/'scripts/update_now.py'),'--nightly'],StartCalendarInterval=dict(Hour=0,Minute=15),WorkingDirectory=str(ROOT),StandardOutPath=str(logs/'nightly.log'),StandardErrorPath=str(logs/'nightly-error.log'),ProcessType='Background',RunAtLoad=False)
target.parent.mkdir(parents=True,exist_ok=True)
target.write_bytes(plistlib.dumps(config));target.chmod(0o600)
domain='gui/%s'%os.getuid()
subprocess.run(['launchctl','bootout',domain+'/'+label],capture_output=True)
subprocess.run(['launchctl','bootstrap',domain,str(target)],check=True)
print('Nightly incremental sync installed: 00:15 local time; yesterday + today; no immediate run.')
