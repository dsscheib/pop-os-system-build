#!/usr/bin/env bash
# Full greenfield end-to-end verification inside a disposable ubuntu:24.04 or ubuntu:26.04 container.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: podman run --rm -v "$PWD:/workspace:ro" docker.io/library/ubuntu:24.04 \
         bash /workspace/scripts/e2e_container_test.sh [/workspace]

Runs full greenfield end-to-end verification inside a disposable ubuntu:24.04
or ubuntu:26.04 container: ./bootstrap.sh, desktop_apps, auth.sh with ephemeral
age/Chezmoi/Neovim fixtures, Day-2 convergence, lazy-lock.json merge, opt-in
stm32, and mise run role dispatch.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      break
      ;;
    -*)
      printf 'Unknown option: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
    *)
      break
      ;;
  esac
done

if [[ ! -f /.dockerenv && ! -f /run/.containerenv ]]; then
  echo "Refusing to run outside a disposable container (neither /.dockerenv nor /run/.containerenv found)." >&2
  usage >&2
  exit 1
fi

if [[ $EUID -ne 0 ]]; then
  echo "This script must start as root inside the container so it can create the unprivileged tester user." >&2
  exit 1
fi

REPO_SRC=${1:-/workspace}
if [[ ! -d "$REPO_SRC" ]]; then
  echo "Repository source directory not found: $REPO_SRC" >&2
  exit 1
fi

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
rm -f /home/tester/repo/ansible/group_vars/all/local.yml
chown -R tester:tester /home/tester/repo

su - tester -s /bin/bash <<'EOF'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
export PATH="$HOME/.local/bin:$PATH"
cd /home/tester/repo
export ANSIBLE_CONFIG="$PWD/ansible/ansible.cfg"

echo "=== [1/10] Bootstrap twice + lint ==="
bash ./bootstrap.sh
bash ./bootstrap.sh
expected_core=$(awk -F'"' '$1 == "ANSIBLE_CORE_VERSION=" { print $2; exit }' ./bootstrap.sh)
expected_lint=$(awk -F'"' '$1 == "ANSIBLE_LINT_VERSION=" { print $2; exit }' ./bootstrap.sh)
test -n "$expected_core"
test -n "$expected_lint"
ansible_ver=$(ansible-playbook --version)
lint_ver=$(ansible-lint --version)
grep -Fq "[core $expected_core]" <<<"$ansible_ver"
grep -Fq "$expected_lint" <<<"$lint_ver"
mise run lint

echo "=== [2/10] Fresh Pass 0 (--check --diff across non-snapper roles including desktop_apps & systemd_live) ==="
ansible-playbook --check --diff -i ansible/inventory/hosts.yml ansible/playbook.yml \
  --skip-tags snapper -e system_apt_upgrade=false

echo "=== [3/10] Create local Chezmoi + Neovim fixtures (ephemeral age key) ==="
mkdir -p "$HOME/.config/chezmoi" /tmp/fixtures
chmod 0700 "$HOME/.config/chezmoi"
age-keygen -o "$HOME/.config/chezmoi/key.txt"
chmod 0600 "$HOME/.config/chezmoi/key.txt"
AGE_PUB=$(age-keygen -y "$HOME/.config/chezmoi/key.txt")

git init --bare -b main /tmp/fixtures/dotfiles.git
mkdir -p /tmp/fixtures/dotfiles-src/dot_config/mise \
         /tmp/fixtures/dotfiles-src/dot_config/rclone \
         /tmp/fixtures/dotfiles-src/dot_config/tmux

cat > /tmp/fixtures/dotfiles-src/.chezmoi.toml.tmpl <<TOML
encryption = "age"

[age]
  identity = "~/.config/chezmoi/key.txt"
  recipient = "$AGE_PUB"
TOML

printf 'GDRIVE_CLIENT_ID="test-client-id"\nGDRIVE_CLIENT_SECRET="test-client-secret"\n' \
  | age -r "$AGE_PUB" -o /tmp/fixtures/dotfiles-src/dot_config/rclone/encrypted_private_gdrive-oauth.env.age

cat > /tmp/fixtures/dotfiles-src/dot_config/mise/config.toml <<'TOML'
[tools]
chezmoi = "latest"
neovim = "0.12.6"
rclone = "1.75.2"
starship = "1.26.0"
"github:herdrdev/herdr" = "0.9.5"
TOML

cat > /tmp/fixtures/dotfiles-src/run_onchange_after_mise-install.sh.tmpl <<'SH'
#!/usr/bin/env bash
# config.toml hash: {{ include "dot_config/mise/config.toml" | sha256sum }}
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
if command -v mise >/dev/null 2>&1; then
  mise install --yes
fi
SH
chmod 0755 /tmp/fixtures/dotfiles-src/run_onchange_after_mise-install.sh.tmpl

cat > /tmp/fixtures/dotfiles-src/dot_config/tmux/tmux.conf <<'TMUX'
set -g @plugin 'tmux-plugins/tpm'
set -g @plugin 'tmux-plugins/tmux-sensible'
TMUX

(
  cd /tmp/fixtures/dotfiles-src || exit 1
  git init -b main
  git -c user.name=Test -c user.email=test@example.com add .
  git -c user.name=Test -c user.email=test@example.com commit -m "initial fixture dotfiles"
  git push /tmp/fixtures/dotfiles.git main
)

git init --bare -b main /tmp/fixtures/nvim.git
mkdir -p /tmp/fixtures/nvim-src/lua /tmp/fixtures/nvim-src/scripts
cat > /tmp/fixtures/nvim-src/init.lua <<'LUA'
vim.api.nvim_create_user_command("Lazy", function() end, { bang = true, nargs = "*" })
LUA
cat > /tmp/fixtures/nvim-src/lua/setup_checks.lua <<'LUA'
return {}
LUA
cat > /tmp/fixtures/nvim-src/lazy-lock.json <<'JSON'
{
  "alpha.nvim": { "branch": "main", "commit": "aaa1111" },
  "beta.nvim": { "branch": "main", "commit": "bbb1111" }
}
JSON
cat > /tmp/fixtures/nvim-src/scripts/setup.sh <<'SH'
#!/usr/bin/env bash
set -euo pipefail
"${NVIM_BIN:?}" --version >/dev/null
mkdir -p "$HOME/.local/share/nvim/lazy/lazy.nvim"
SH
chmod 0755 /tmp/fixtures/nvim-src/scripts/setup.sh
(
  cd /tmp/fixtures/nvim-src || exit 1
  git init -b main
  git -c user.name=Test -c user.email=test@example.com add .
  git -c user.name=Test -c user.email=test@example.com commit -m "initial fixture nvim"
  git push /tmp/fixtures/nvim.git main
)

cat > /home/tester/repo/ansible/group_vars/all/local.yml <<'YAML'
---
dotfiles_repo: "/tmp/fixtures/dotfiles.git"
nvim_config_repo: "/tmp/fixtures/nvim.git"
YAML

echo "=== [4/10] Day-1 Pass 1 Apply (including desktop_apps minus flatpak) ==="
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbook.yml \
  --skip-tags snapper,systemd_live,flatpak -e system_apt_upgrade=false

echo "=== [4b] Probe flatpak behavior in container (expected: report cleanly if no session dbus) ==="
set +e
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbook.yml --tags flatpak
flatpak_rc=$?
set -e
echo "Flatpak container probe exit code: $flatpak_rc"

echo "=== [5/10] Run mise run check (0 skipped dependencies) inside converged container ==="
mise run check

echo "=== [6/10] Fresh auth flow (chezmoi init + age decrypt + Mise install + herdr) ==="
if command -v chezmoi >/dev/null 2>&1; then
  echo "Unexpected: chezmoi already present before auth" >&2
  exit 1
fi
bash ./scripts/auth.sh --step chezmoi
export PATH="$HOME/.local/bin:$HOME/.local/share/mise/shims:$PATH"

test -f "$HOME/.config/rclone/gdrive-oauth.env"
[[ $(stat -c %a "$HOME/.config/rclone/gdrive-oauth.env") == "600" ]]
grep -Fq 'GDRIVE_CLIENT_ID="test-client-id"' "$HOME/.config/rclone/gdrive-oauth.env"
test -z "$(chezmoi status)"
for cmd in chezmoi nvim rclone starship herdr; do
  command -v "$cmd" >/dev/null
done
bash ./scripts/auth.sh --step herdr
GDRIVE_OAUTH_ENV=/tmp/no-such-oauth.env GDRIVE_CLIENT_ID='' GDRIVE_CLIENT_SECRET='' \
  bash ./scripts/auth.sh --step gdrive </dev/null
if bash ./scripts/auth.sh --step gdrive </dev/null; then
  echo "Expected non-TTY gdrive auth without remote to return non-zero." >&2
  exit 1
fi

echo "=== [7/10] Day-2 Pass 2 Apply (TPM plugins, Neovim post-sync, completions, Herdr skill/hooks) ==="
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbook.yml \
  --skip-tags snapper,systemd_live,flatpak -e system_apt_upgrade=false

test -d "$HOME/.local/share/tmux/plugins/tmux-sensible"
test -d "$HOME/.local/share/nvim/lazy/lazy.nvim"
test -f "$HOME/.local/share/bash-completion/completions/herdr"
test -f "$HOME/.zsh/completion/_herdr"
test -f "$HOME/.config/fish/completions/herdr.fish"
test -f "$HOME/.agents/skills/herdr/SKILL.md"
test -f "$HOME/.claude/skills/herdr/SKILL.md"
test -f "$HOME/.gemini/config/skills/herdr/SKILL.md"

echo "=== [8/10] Post-convergence --check --diff AND Pass 3 Repeat Apply (must be changed=0, failed=0) ==="
check_log=$(mktemp)
pass3_log=$(mktemp)
trap 'rm -f -- "$check_log" "$pass3_log"' EXIT

ansible-playbook --check --diff -i ansible/inventory/hosts.yml ansible/playbook.yml \
  --skip-tags snapper,systemd_live,flatpak -e system_apt_upgrade=false | tee "$check_log"
grep -Eq 'changed=0[[:space:]].*failed=0' "$check_log"

ansible-playbook --diff -i ansible/inventory/hosts.yml ansible/playbook.yml \
  --skip-tags snapper,systemd_live,flatpak -e system_apt_upgrade=false | tee "$pass3_log"
grep -Eq 'changed=0[[:space:]].*failed=0' "$pass3_log"

echo "=== [8b] Test merge_lazy_lock three-way merge via workstation role ==="
cat > "$HOME/.config/nvim/lazy-lock.json" <<'JSON'
{
  "alpha.nvim": { "branch": "main", "commit": "local-alpha" },
  "beta.nvim": { "branch": "main", "commit": "bbb1111" }
}
JSON
cat > /tmp/fixtures/nvim-src/lazy-lock.json <<'JSON'
{
  "alpha.nvim": { "branch": "main", "commit": "aaa1111" },
  "beta.nvim": { "branch": "main", "commit": "upstream-beta" }
}
JSON
(
  cd /tmp/fixtures/nvim-src || exit 1
  git -c user.name=Test -c user.email=test@example.com commit -am "advance beta upstream"
  git push /tmp/fixtures/nvim.git main
)
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbook.yml --tags workstation
python3 - <<'PY'
import json
from pathlib import Path
lock = json.loads(Path.home().joinpath('.config/nvim/lazy-lock.json').read_text())
assert lock['alpha.nvim']['commit'] == 'local-alpha', lock
assert lock['beta.nvim']['commit'] == 'upstream-beta', lock
PY
check_nvim_log=$(mktemp)
ansible-playbook --check --diff -i ansible/inventory/hosts.yml ansible/playbook.yml --tags workstation | tee "$check_nvim_log"
grep -Eq 'changed=0[[:space:]].*failed=0' "$check_nvim_log"
rm -f "$check_nvim_log"

echo "=== [9/10] Opt-in stm32 role tests (missing warning + synthetic CubeMX archive) ==="
ansible-playbook -i ansible/inventory/hosts.yml ansible/playbook.yml --tags stm32
for r in 49-stlinkv1.rules 49-stlinkv2.rules 49-stlinkv2-1.rules 49-stlinkv3.rules; do
  test -f "/etc/udev/rules.d/$r"
done
stm32_chk=$(mktemp)
ansible-playbook --check --diff -i ansible/inventory/hosts.yml ansible/playbook.yml --tags stm32 | tee "$stm32_chk"
grep -Eq 'changed=0[[:space:]].*failed=0' "$stm32_chk"
rm -f "$stm32_chk"

python3 - <<'PY'
import hashlib, stat, zipfile
from pathlib import Path
import xml.etree.ElementTree as ET

repo = Path('/home/tester/repo')
tree = ET.parse(repo / 'resources/izpack/cubemx.xml')
jar_path = Path('/tmp/fixtures/SetupSTM32CubeMX-test')
with zipfile.ZipFile(jar_path, 'w') as z:
    z.writestr('resources/panelsOrder', ' '.join(x.attrib['id'] for x in tree.getroot()))
    for tag in dict.fromkeys(el.tag for el in tree.getroot()):
        z.writestr(tag.replace('.', '/') + '.class', '')

dl_dir = Path.home() / 'Downloads'
dl_dir.mkdir(parents=True, exist_ok=True)
archive_path = dl_dir / 'SetupSTM32CubeMX-test.zip'
fake_java = '''#!/usr/bin/env bash
python3 - "$4" <<'INNER'
from pathlib import Path
import shutil, sys, xml.etree.ElementTree as ET
target = Path(ET.parse(sys.argv[1]).find('.//installpath').text)
exe = target / 'STM32CubeMX'
exe.write_text('#!/bin/sh\\nexit 0\\n')
exe.chmod(0o755)
shutil.copytree('jre', target / 'jre')
INNER
'''
with zipfile.ZipFile(archive_path, 'w') as z:
    z.write(jar_path, jar_path.name)
    info = zipfile.ZipInfo('jre/bin/java')
    info.external_attr = (stat.S_IFREG | 0o755) << 16
    z.writestr(info, fake_java)

sha = hashlib.sha256(archive_path.read_bytes()).hexdigest()
Path('/tmp/fixtures/cubemx.sha256').write_text(sha)
PY

ST_INSTALLER_SHA256=$(< /tmp/fixtures/cubemx.sha256) \
  ansible-playbook -i ansible/inventory/hosts.yml ansible/playbook.yml \
  --tags stm32 -e 'stm32_tools=["cubemx"]'
test -x "$HOME/.local/bin/stm32cubemx"
desktop-file-validate "$HOME/.local/share/applications/st-com-stm32cubemx.desktop"

echo "=== [10/10] Verify installed binaries, wrappers, fonts, venvs, and mise role arg dispatch ==="
gcc --version >/dev/null
g++ --version >/dev/null
clang --version >/dev/null
podman --version >/dev/null
gh --version >/dev/null
firefox-devedition --version >/dev/null
google-chrome --version >/dev/null
brave-browser --version >/dev/null
code --version --no-sandbox >/dev/null
kicad-cli --version >/dev/null
"$HOME/.local/share/kicad-python/bin/python" -c 'import kipy'
"$HOME/.local/share/nvim-python/bin/python" -c 'import pynvim'
claude --version >/dev/null
agy --version >/dev/null
test -x "$HOME/.local/bin/arduino-ide"
desktop-file-validate "$HOME/.local/share/applications/arduino-ide.desktop"
test -x "$HOME/.local/bin/saleae-logic"
desktop-file-validate "$HOME/.local/share/applications/saleae-logic.desktop"
test -x "$HOME/.local/bin/hyper-backup-explorer"
desktop-file-validate "$HOME/.local/share/applications/synology-hyper-backup-explorer.desktop"
dpkg_out=$(dpkg-query -W -f='${Status}|${Version}\n' synology-drive)
grep -Fq 'install ok installed|' <<<"$dpkg_out"
fc_out=$(fc-list)
grep -qi 'JetBrainsMono' <<<"$fc_out"

# Verify real `mise run role` dispatch through mise + scripts/ansible_run.sh + ansible-playbook
python3 - <<'PY'
import os, pty

pid, fd = pty.fork()
if pid == 0:
    os.execvp('mise', ['mise', 'run', 'role', 'completions', '--', '--check'])
out = bytearray()
sent = False
while True:
    try:
        chunk = os.read(fd, 4096)
        if not chunk:
            break
        out.extend(chunk)
        if not sent and b'BECOME password:' in out:
            os.write(fd, b'\n')
            sent = True
    except OSError:
        break
_, status = os.waitpid(pid, 0)
assert os.waitstatus_to_exitcode(status) == 0, out.decode(errors='replace')
print("Verified real `mise run role completions -- --check` via PTY.")
PY

echo "=== ALL GREENFIELD CONTAINER E2E CHECKS PASSED ==="
EOF
