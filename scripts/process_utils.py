#!/usr/bin/env python3
"""Subprocess helpers with recursive child-process cleanup."""
import os
import signal
import subprocess
import time

_ACTIVE_ROOTS=set()
_INSTALLED=False

def _pid_tree(root_pid):
    try:
        output=subprocess.check_output(
            ['ps','-axo','pid=,ppid='],text=True,stderr=subprocess.DEVNULL)
    except Exception:
        return [root_pid]
    children={}
    for line in output.splitlines():
        parts=line.split()
        if len(parts)!=2:
            continue
        try:
            pid,ppid=map(int,parts)
        except ValueError:
            continue
        children.setdefault(ppid,[]).append(pid)
    result=[]
    def walk(pid):
        for child in children.get(pid,()):
            walk(child)
            result.append(child)
    walk(root_pid)
    result.append(root_pid)
    return list(dict.fromkeys(result))
def _alive(pid):
    try:
        os.kill(pid,0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True

def _kill_tree(root_pid):
    targets=_pid_tree(root_pid)
    for pid in targets:
        try:
            os.kill(pid,signal.SIGTERM)
        except (ProcessLookupError,PermissionError):
            pass
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        if not any(_alive(pid) for pid in targets):
            return
        time.sleep(0.1)
    for pid in targets:
        if not _alive(pid):
            continue
        try:
            os.kill(pid,signal.SIGKILL)
        except (ProcessLookupError,PermissionError):
            pass

def install_signal_cleanup():
    global _INSTALLED
    if _INSTALLED:
        return
    def handler(signum,frame):
        for pid in list(_ACTIVE_ROOTS):
            _kill_tree(pid)
        raise SystemExit(128+signum)
    signal.signal(signal.SIGTERM,handler)
    signal.signal(signal.SIGINT,handler)
    _INSTALLED=True
def run_group(args, *, timeout, input=None, stdin=None, text=True):
    if input is not None and stdin is not None:
        raise ValueError('input and explicit stdin are mutually exclusive')
    child_stdin=subprocess.PIPE if input is not None else stdin
    proc=subprocess.Popen(
        args,stdin=child_stdin,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        text=text,start_new_session=True)
    _ACTIVE_ROOTS.add(proc.pid)
    try:
        try:
            stdout,stderr=proc.communicate(input=input,timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(proc.pid)
            try:
                stdout,stderr=proc.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                stdout,stderr='',''
            raise subprocess.TimeoutExpired(args,timeout,output=stdout,stderr=stderr)
        return subprocess.CompletedProcess(args,proc.returncode,stdout,stderr)
    finally:
        _ACTIVE_ROOTS.discard(proc.pid)
