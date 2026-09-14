"""Required, private archive-owner configuration; no built-in identity."""
import json
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ConfigError(ValueError):
    pass


def load_identity():
    configured = os.environ.get('OURSTEPS_CONFIG')
    if configured is not None and not configured.strip():
        raise ConfigError('OURSTEPS_CONFIG must name a configuration file')
    path = Path(configured).expanduser() if configured is not None else PROJECT_ROOT / 'config.local.json'
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ConfigError(f'Cannot load OurSteps identity config: {path}; provide valid uid, username and display_name') from exc
    if (not isinstance(data, dict) or type(data.get('uid')) is not int
            or not 0 < data['uid'] <= 9223372036854775807
            or any(not isinstance(data.get(key), str) or not data[key].strip()
                   for key in ('username', 'display_name'))):
        raise ConfigError(f'Invalid OurSteps identity config: {path}; uid must be a positive SQLite integer and username/display_name must be nonempty strings')
    return data['uid'], data['username'], data['display_name']


UID, USERNAME, DISPLAY_NAME = load_identity()
