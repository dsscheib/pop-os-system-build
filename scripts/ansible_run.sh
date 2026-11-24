#!/usr/bin/env bash
# Dispatch Mise Ansible tasks with real shell positional arguments.
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
export ANSIBLE_CONFIG="$REPO_ROOT/ansible/ansible.cfg"

usage() {
  cat <<'EOF' >&2
Usage: ./scripts/ansible_run.sh <apply|plan|role|lint> [args...]
  role requires: <tag[,tag2]> [extra ansible-playbook args...]
EOF
}

require_ansible_playbook() {
  command -v ansible-playbook >/dev/null 2>&1 || {
    echo "ansible-playbook not found. Run ./bootstrap.sh first." >&2
    exit 1
  }
}

action=${1:-}
if [[ -z $action ]]; then
  usage
  exit 2
fi
shift

case "$action" in
  apply)
    require_ansible_playbook
    exec ansible-playbook -i "$REPO_ROOT/ansible/inventory/hosts.yml" \
      "$REPO_ROOT/ansible/playbook.yml" --ask-become-pass "$@"
    ;;
  plan)
    require_ansible_playbook
    exec ansible-playbook -i "$REPO_ROOT/ansible/inventory/hosts.yml" \
      "$REPO_ROOT/ansible/playbook.yml" --check --diff --ask-become-pass "$@"
    ;;
  role)
    if [[ $# -eq 0 || -z ${1:-} ]]; then
      echo "Usage: mise run role <tag[,tag2]> [extra ansible-playbook args...]" >&2
      exit 2
    fi
    tags=$1
    shift
    require_ansible_playbook
    exec ansible-playbook -i "$REPO_ROOT/ansible/inventory/hosts.yml" \
      "$REPO_ROOT/ansible/playbook.yml" --ask-become-pass --tags "$tags" "$@"
    ;;
  lint)
    require_ansible_playbook
    ansible-playbook -i "$REPO_ROOT/ansible/inventory/hosts.yml" \
      --syntax-check "$REPO_ROOT/ansible/playbook.yml"
    if command -v ansible-lint >/dev/null 2>&1; then
      (cd "$REPO_ROOT/ansible" && ansible-lint)
    else
      echo "[WARN] ansible-lint is not installed; skipped lint check." >&2
    fi
    ;;
  *)
    usage
    exit 2
    ;;
esac
