#!/usr/bin/env python3
"""Install Mac LaunchAgents for safe Control Center actions and worker status."""
import os,plistlib,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if sys.platform!='darwin': raise SystemExit('Run on the Mac mini')
logs=Path.home()/'Library/Logs';logs.mkdir(exist_ok=True);domain='gui/%s'%os.getuid()

def install(label,args,interval,out,err,run_at_load=False):
    target=Path.home()/'Library/LaunchAgents'/(label+'.plist')
    config=dict(Label=label,ProgramArguments=args,StartInterval=interval,WorkingDirectory=str(ROOT),StandardOutPath=str(logs/out),StandardErrorPath=str(logs/err),ProcessType='Background',RunAtLoad=run_at_load)
    target.write_bytes(plistlib.dumps(config));target.chmod(0o600)
    subprocess.run(['launchctl','bootout',domain+'/'+label],capture_output=True)
    subprocess.run(['launchctl','bootstrap',domain,str(target)],check=True)

install('local.oursteps.control-actions',[sys.executable,str(ROOT/'scripts/control_action_runner.py')],60,'oursteps-control-actions.log','oursteps-control-actions-error.log')
install('local.oursteps.worker-status',[sys.executable,str(ROOT/'scripts/worker_status_snapshot.py')],30,'oursteps-worker-status.log','oursteps-worker-status-error.log',run_at_load=True)
print('Installed Control Center action runner (60s) and worker status snapshot (30s)')
