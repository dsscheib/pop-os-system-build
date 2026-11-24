#!/usr/bin/env bash
# Run inside an ubuntu:24.04 CI container to verify playbook convergence and idempotency.
set -euo pipefail

REPO_SRC=${1:-/workspace}
export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y sudo

if ! id -u tester >/dev/null 2>&1; then
  useradd -m -s /bin/bash tester
fi
cat > /etc/sudoers.d/tester <<'EOF'
Defaults env_keep += "DEBIAN_FRONTEND"
tester ALL=(ALL) NOPASSWD:ALL
EOF
chmod 0440 /etc/sudoers.d/tester
usermod --add-subuids 100000-165535 --add-subgids 100000-165535 tester

rm -rf /home/tester/repo
cp -a "$REPO_SRC" /home/tester/repo
chown -R tester:tester /home/tester/repo

su - tester -s /bin/bash <<'EOF'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
export PATH="$HOME/.local/bin:$PATH"
cd /home/tester/repo

echo "=== Bootstrap (APT prereqs, uv, mise, pinned ansible-core/ansible-lint) ==="
bash ./bootstrap.sh
mise run lint

export ANSIBLE_CONFIG="$PWD/ansible/ansible.cfg"

echo "=== Ansible Pass 0 (fresh --check --diff) ==="
ansible-playbook --check --diff -i ansible/inventory/hosts.yml ansible/playbook.yml \
  --skip-tags snapper,systemd_live,desktop_gui \
  -e system_apt_upgrade=false

echo "=== Ansible Pass 1 (initial convergence) ==="
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbook.yml \
  --skip-tags snapper,systemd_live,desktop_gui \
  -e system_apt_upgrade=false

echo "=== Ansible Pass 2 (post-convergence --check --diff idempotency) ==="
check_log=$(mktemp)
pass3_log=$(mktemp)
trap 'rm -f -- "$check_log" "$pass3_log"' EXIT
ansible-playbook --check --diff -i ansible/inventory/hosts.yml ansible/playbook.yml \
  --skip-tags snapper,systemd_live,desktop_gui \
  -e system_apt_upgrade=false | tee "$check_log"

grep -Eq 'changed=0[[:space:]].*failed=0' "$check_log" || {
  echo "Expected changed=0 and failed=0 on post-convergence --check pass." >&2
  exit 1
}

echo "=== Ansible Pass 3 (apply idempotency check) ==="
ansible-playbook --diff -i ansible/inventory/hosts.yml ansible/playbook.yml \
  --skip-tags snapper,systemd_live,desktop_gui \
  -e system_apt_upgrade=false | tee "$pass3_log"

grep -Eq 'changed=0[[:space:]].*failed=0' "$pass3_log" || {
  echo "Expected changed=0 and failed=0 on second apply pass." >&2
  exit 1
}
EOF
