#!/usr/bin/env python3
"""Append one managed source statement while preserving user startup files."""
import os
from pathlib import Path
import sys
from st_assets import atomic_text


def integrate(home, resources):
    home, resources = Path(home), Path(resources)
    destination = home / '.config/pop-workstation'
    for shell in ('bash', 'zsh', 'fish'):
        atomic_text(destination / shell, (resources / f'shell.{shell}').read_text(), 0o644)
    for name, shell in (('.bashrc', 'bash'), ('.zshenv', 'zsh')):
        path = home / name
        # A linked startup file may belong to a Git-managed dotfiles checkout.
        # Leave that checkout clean and give an explicit integration instruction.
        line = f'[ ! -f "$HOME/.config/pop-workstation/{shell}" ] || source "$HOME/.config/pop-workstation/{shell}"'
        if path.is_symlink():
            print(f'Add to your linked {name}: {line}', file=sys.stderr)
            continue
        text = path.read_text() if path.exists() else ''
        if '.config/pop-workstation/' not in text:
            mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
            atomic_text(path, text.rstrip() + '\n\n# Pop workstation tools\n' + line + '\n', mode)
    # Fish discovers conf.d files automatically.
    atomic_text(home / '.config/fish/conf.d/pop-workstation.fish',
                'source "$HOME/.config/pop-workstation/fish"\n', 0o644)


if __name__ == '__main__':
    integrate(*sys.argv[1:])
