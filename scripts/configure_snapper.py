#!/usr/bin/env python3
"""Update Snapper shell-style config as data; preserve other registered configs."""
from pathlib import Path
import re
import shlex
import sys
from st_assets import atomic_text


def read_assignments(text):
    return {m[1]: shlex.split(m[2])[0] if shlex.split(m[2]) else ''
            for m in re.finditer(r'^([A-Z_]+)=(.*)$', text, re.MULTILINE)}


def update(text, settings):
    for key, value in settings.items():
        replacement = key + '=' + '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'
        pattern = r'^' + re.escape(key) + '=.*$'
        if re.search(pattern, text, re.MULTILINE):
            text = re.sub(pattern, lambda _: replacement, text, flags=re.MULTILINE)
        else:
            text = text.rstrip() + '\n' + replacement + '\n'
    return text


def configure(root_config, default_config, initialize=False):
    root = Path(root_config)
    defaults = Path(default_config)
    text = root.read_text()
    values = read_assignments(text)
    if not initialize and values.get('SUBVOLUME', '/') != '/':
        raise ValueError('Existing root Snapper config describes another subvolume')
    settings = {'SUBVOLUME': '/', 'FSTYPE': 'btrfs'}
    if initialize:
        settings.update(NUMBER_CLEANUP='yes', NUMBER_MIN_AGE='1800', NUMBER_LIMIT='10',
                        NUMBER_LIMIT_IMPORTANT='5', TIMELINE_CREATE='no', TIMELINE_CLEANUP='yes')
    # Existing retention and access settings are preserved. Add the admin group.
    import grp
    group = 'sudo' if any(g.gr_name == 'sudo' for g in grp.getgrall()) else 'root'
    groups = list(dict.fromkeys(values.get('ALLOW_GROUPS', '').split() + [group]))
    settings.update(ALLOW_GROUPS=' '.join(groups), SYNC_ACL='yes')
    root_backup = root.with_name(root.name + '.pre-pop-setup')
    if not root_backup.exists():
        atomic_text(root_backup, text, 0o600)
    atomic_text(root, update(text, settings), 0o640)
    defaults_text = defaults.read_text() if defaults.exists() else ''
    default_values = read_assignments(defaults_text)
    names = list(dict.fromkeys(default_values.get('SNAPPER_CONFIGS', '').split() + ['root']))
    defaults_backup = defaults.with_name(defaults.name + '.pre-pop-setup')
    if not defaults_backup.exists():
        atomic_text(defaults_backup, defaults_text, 0o600)
    # Disable distribution-provided APT hooks; our helper provides the paired ones.
    atomic_text(defaults, update(defaults_text, {'SNAPPER_CONFIGS': ' '.join(names), 'DISABLE_APT_SNAPSHOT': 'yes'}), 0o644)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--initialize', action='store_true')
    parser.add_argument('root', nargs='?', default='/etc/snapper/configs/root')
    parser.add_argument('defaults', nargs='?', default='/etc/default/snapper')
    args = parser.parse_args()
    configure(args.root, args.defaults, args.initialize)
