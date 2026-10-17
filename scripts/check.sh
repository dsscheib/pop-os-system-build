#!/usr/bin/env bash
# Static and fixture checks only. No installers, disk mounts or system changes.
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$SCRIPT_DIR/.."
for tool in shellcheck python3 apt-config desktop-file-validate; do
  command -v "$tool" >/dev/null || { printf 'Missing test dependency: %s\n' "$tool" >&2; exit 1; }
done
shellcheck -x ./*.sh scripts/*.sh resources/*.bash resources/completions/*.bash
for script in ./*.sh scripts/*.sh resources/*.bash resources/completions/*.bash; do bash -n "$script"; done
python3 -B -m unittest discover -s tests -v
python3 -B - <<'PY'
import ast
from pathlib import Path
for folder in ('scripts', 'tests'):
    for path in Path(folder).glob('*.py'):
        ast.parse(path.read_text(), filename=str(path))
PY
if command -v fish >/dev/null; then
  for script in resources/*.fish resources/completions/*.fish; do fish --no-execute "$script"; done
fi
if command -v zsh >/dev/null; then
  for script in resources/_django resources/*.zsh resources/completions/*.zsh; do zsh -n "$script"; done
fi
git diff --check
