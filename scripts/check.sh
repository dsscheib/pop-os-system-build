#!/usr/bin/env bash
# Static and fixture checks only. No installers, disk mounts or system changes.
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$SCRIPT_DIR/.."
for tool in shellcheck python3 apt-config desktop-file-validate; do
  command -v "$tool" >/dev/null || { printf 'Missing test dependency: %s\n' "$tool" >&2; exit 1; }
done
shellcheck -x ./*.sh preboot/*.sh scripts/*.sh
for script in ./*.sh preboot/*.sh scripts/*.sh; do bash -n "$script"; done
python3 -B -m unittest discover -s tests -v
python3 -B - <<'PY'
import ast
from pathlib import Path
import yaml

for folder in ('scripts', 'tests'):
    for path in Path(folder).glob('*.py'):
        ast.parse(path.read_text(), filename=str(path))

for path in sorted(Path('ansible').rglob('*.yml')):
    yaml.safe_load(path.read_text())
PY
if command -v ansible-playbook >/dev/null; then
  ANSIBLE_CONFIG="$PWD/ansible/ansible.cfg" ansible-playbook -i ansible/inventory/hosts.yml --syntax-check ansible/playbook.yml
fi
if command -v ansible-lint >/dev/null; then
  (cd ansible && ansible-lint)
fi
git diff --check
