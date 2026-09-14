"""Deployment-only configuration, also copied beside standalone Mac tools."""
import json
import os
from pathlib import Path
import re
import shlex


def config_section(section):
    module = Path(__file__).resolve()
    default = (module.parents[1] / 'config.local.json' if module.name == 'deployment.py'
               else module.with_name('oursteps-config.local.json'))
    configured = os.environ.get('OURSTEPS_CONFIG')
    if configured is not None and not configured.strip():
        raise ValueError('OURSTEPS_CONFIG must name a configuration file')
    path = Path(configured).expanduser() if configured is not None else default
    try:
        data = json.loads(path.read_text(encoding='utf-8'))[section]
        if not isinstance(data, dict):
            raise ValueError('section must be an object')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError('Missing/invalid ' + section + ' config: ' + str(path)) from exc
    return data


def public_access():
    data = config_section('public_access')
    users = tuple(data.get(key) for key in ('recent_user', 'full_user'))
    if any(not isinstance(user, str) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_-]{0,63}', user) for user in users):
        raise ValueError('Invalid public_access usernames: use 1-64 ASCII letters, digits, underscores or hyphens; no leading hyphen')
    if users[0] == users[1]:
        raise ValueError('public_access usernames must be different')
    return users


def deployment():
    """Environment overrides private JSON; legacy variable names remain aliases."""
    host = os.environ.get('OURSTEPS_NAS_HOST', os.environ.get('OURSTEPS_NAS'))
    project = os.environ.get('OURSTEPS_NAS_PROJECT', os.environ.get('OURSTEPS_NAS_ROOT'))
    if host is None or project is None:
        data = config_section('deployment')
        host = data.get('nas_host') if host is None else host
        project = data.get('nas_project') if project is None else project
    if not isinstance(host, str) or not re.fullmatch(r'(?:[A-Za-z0-9_][A-Za-z0-9_.-]*@)?[A-Za-z0-9][A-Za-z0-9.-]*', host):
        raise ValueError('Invalid deployment nas_host: expected SSH host or user@host')
    if not isinstance(project, str) or not project.startswith('/') or any(c in project for c in '\x00\r\n'):
        raise ValueError('Invalid deployment nas_project: expected an absolute remote project path')
    return host, project


def remote_python(*args):
    return 'cd ' + shlex.quote(deployment()[1]) + ' && python3 ' + shlex.join(args)
