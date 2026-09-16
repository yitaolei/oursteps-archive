#!/usr/bin/env python3
"""Install Mac LaunchAgent that safely drains one Control Center action per minute."""
import os,plistlib,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if sys.platform!='darwin': raise SystemExit('Run on the Mac mini')
label='local.oursteps.control-actions';target=Path.home()/'Library/LaunchAgents'/(label+'.plist');logs=Path.home()/'Library/Logs';logs.mkdir(exist_ok=True)
config=dict(Label=label,ProgramArguments=[sys.executable,str(ROOT/'scripts/control_action_runner.py')],StartInterval=60,WorkingDirectory=str(ROOT),StandardOutPath=str(logs/'oursteps-control-actions.log'),StandardErrorPath=str(logs/'oursteps-control-actions-error.log'),ProcessType='Background',RunAtLoad=False)
target.write_bytes(plistlib.dumps(config));target.chmod(0o600);domain='gui/%s'%os.getuid();subprocess.run(['launchctl','bootout',domain+'/'+label],capture_output=True);subprocess.run(['launchctl','bootstrap',domain,str(target)],check=True);print('Installed '+label+' every 60 seconds')
