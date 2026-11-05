#!/usr/bin/env python3
"""Merge the agent-guidance command guardrails into Antigravity CLI settings.

Adds remote and destructive git commands to permissions.ask in
~/.gemini/antigravity-cli/settings.json. Antigravity evaluates deny > ask >
allow, so these prompts win over broader allow rules. Every other setting and
rule is kept; a settings file that is not a JSON object is left untouched.
"""
import argparse
import json
import os
from pathlib import Path

from st_assets import atomic_text

# Mirrors the Antigravity CLI rules in agent-guidance docs/AGENT_TOOLS.md.
ASK_RULES = [
    'command(git push)',
    'command(git reset --hard)',
    'command(git clean)',
    'command(git branch -D)',
    'command(git rebase)',
    'command(git checkout --)',
]


class SettingsError(Exception):
    pass


def merge(settings):
    """Return (settings, added) with ASK_RULES present in permissions.ask."""
    if not isinstance(settings, dict):
        raise SettingsError('settings is not a JSON object')
    permissions = settings.setdefault('permissions', {})
    if not isinstance(permissions, dict):
        raise SettingsError('"permissions" is not a JSON object')
    ask = permissions.setdefault('ask', [])
    if not isinstance(ask, list):
        raise SettingsError('"permissions.ask" is not a JSON array')
    added = [rule for rule in ASK_RULES if rule not in ask]
    ask.extend(added)
    return settings, added


def apply(path):
    path = Path(path)
    if path.exists():
        try:
            settings = json.loads(path.read_text(encoding='utf-8'))
        except json.JSONDecodeError as error:
            raise SettingsError(f'{path} is not valid JSON ({error}); fix it or add the rules with /permissions')
        mode = path.stat().st_mode & 0o777
    else:
        settings, mode = {}, 0o600
    try:
        settings, added = merge(settings)
    except SettingsError as error:
        raise SettingsError(f'{path}: {error}; fix it or add the rules with /permissions')
    if added:
        atomic_text(path, json.dumps(settings, indent=2) + '\n', mode)
    return added


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('settings', nargs='?',
                        default=os.path.expanduser('~/.gemini/antigravity-cli/settings.json'))
    args = parser.parse_args()
    try:
        added = apply(args.settings)
    except SettingsError as error:
        raise SystemExit(f'Antigravity guardrails not applied: {error}')
    if added:
        print(f'Added {len(added)} Antigravity ask rule(s) to {args.settings}: ' + ', '.join(added))
    else:
        print(f'Antigravity ask rules already present in {args.settings}')


if __name__ == '__main__':
    main()
