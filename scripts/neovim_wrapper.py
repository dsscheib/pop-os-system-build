#!/usr/bin/env python3
"""Keep project cwd and select the dedicated Python provider before user config."""
import shlex
import sys
from st_assets import atomic_text


def write(destination, executable, python):
    command = "let g:python3_host_prog='" + python.replace("'", "''") + "'"
    atomic_text(destination, '#!/usr/bin/env bash\nset -euo pipefail\nexec ' +
                shlex.quote(executable) + ' --cmd ' + shlex.quote(command) + ' "$@"\n', 0o755)


if __name__ == '__main__':
    write(*sys.argv[1:])
