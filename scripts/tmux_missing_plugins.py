#!/usr/bin/env python3
"""Print declared @plugin names in tmux configuration missing from the TPM plugins directory."""
from pathlib import Path
import re
import sys

PLUGIN_LINE_RE = re.compile(r'^set(?:-option)?\s+-g\s+@plugin\s+')


def missing_plugins(home):
    """Return declared plugin directory names missing under ~/.local/share/tmux/plugins."""
    home_path = Path(home)
    plugins_dir = home_path / '.local/share/tmux/plugins'
    confs = (home_path / '.tmux.conf', home_path / '.config/tmux/tmux.conf')
    txt = '\n'.join(c.read_text(encoding='utf-8') for c in confs if c.exists())
    lines = [
        PLUGIN_LINE_RE.sub('', ln.strip())
        for ln in txt.splitlines()
        if PLUGIN_LINE_RE.match(ln.strip())
    ]
    plugins = [
        v.split('#', 1)[0].strip(" \t'\"").rstrip('/').split('/')[-1].removesuffix('.git')
        for v in lines
    ]
    return [p for p in plugins if p and not (plugins_dir / p).is_dir()]


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        raise SystemExit('Usage: tmux_missing_plugins.py <home>')
    for plugin in missing_plugins(args[0]):
        print(plugin)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
