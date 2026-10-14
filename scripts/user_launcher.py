#!/usr/bin/env python3
"""Write a user-space launcher that keeps the caller's working directory."""
import argparse
import shlex

from st_assets import atomic_text


def write(destination, command):
    content = '#!/usr/bin/env bash\nset -euo pipefail\nexec ' + shlex.join(command) + ' "$@"\n'
    atomic_text(destination, content, 0o755)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination')
    parser.add_argument('command', nargs='+')
    args = parser.parse_args()
    write(args.destination, args.command)
