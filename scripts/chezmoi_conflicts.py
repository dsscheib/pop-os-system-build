#!/usr/bin/env python3
"""Print targets that `chezmoi apply` would stop to ask about, read from `chezmoi status` on stdin.

The first status column compares the last state chezmoi wrote with the actual
state; the second compares the actual state with the target. When both are set,
something else changed the target since chezmoi wrote it and chezmoi still wants
to change it, so `chezmoi apply` prompts (and fails with EOF under --no-tty).
"""
import sys


def conflicts(status_text):
    """Return target paths whose `chezmoi status` line shows a change on both sides."""
    paths = []
    for line in status_text.splitlines():
        if len(line) < 4 or line[2] != ' ':
            continue
        if line[0] != ' ' and line[1] != ' ':
            paths.append(line[3:])
    return paths


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if args:
        raise SystemExit('Usage: chezmoi status | chezmoi_conflicts.py')
    for path in conflicts(sys.stdin.read()):
        print(path)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
