#!/usr/bin/env bash
# Run inside an ubuntu:24.04 CI container to verify playbook convergence and idempotency.
set -euo pipefail

REPO_SRC=${1:-/workspace}
export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y sudo python3 python3-venv python3-yaml curl git ca-certificates pipx

if ! id -u tester >/dev/null 2>&1; then
  useradd -m -s /bin/bash tester
fi
echo 'tester ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/tester
chmod 0440 /etc/sudoers.d/tester
usermod --add-subuids 100000-165535 --add-subgids 100000-165535 tester

rm -rf /home/tester/repo
cp -a "$REPO_SRC" /home/tester/repo
chown -R tester:tester /home/tester/repo

su - tester -s /bin/bash <<'EOF'
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
pipx install "ansible-core==2.22.0"
cd /home/tester/repo
ansible-galaxy collection install -r ansible/requirements.yml
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
