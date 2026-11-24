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
pipx install ansible-core
cd /home/tester/repo
ansible-galaxy collection install -r ansible/requirements.yml
export ANSIBLE_CONFIG="$PWD/ansible/ansible.cfg"

echo "=== Ansible Pass 1 (initial convergence) ==="
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbook.yml \
  --skip-tags snapper,systemd_live,desktop_gui \
  -e system_apt_upgrade=false

echo "=== Ansible Pass 2 (idempotency check) ==="
pass2_log=$(mktemp)
trap 'rm -f -- "$pass2_log"' EXIT
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbook.yml \
  --skip-tags snapper,systemd_live,desktop_gui \
  -e system_apt_upgrade=false | tee "$pass2_log"

grep -Eq 'changed=0[[:space:]].*failed=0' "$pass2_log" || {
  echo "Expected changed=0 and failed=0 on second pass." >&2
  exit 1
}
EOF
