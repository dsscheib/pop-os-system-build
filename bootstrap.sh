#!/usr/bin/env bash
# Bootstrap v2 workstation tooling (APT prerequisites, uv, mise, ansible-core).
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

MISE_VERSION="v2026.10.9"
MISE_URL="https://github.com/jdx/mise/releases/download/v2026.10.9/mise-v2026.10.9-linux-x64"
MISE_SHA256="6ab6646b52b83f6bbd51c9d0d3ae54264ffa90efd1ac6d7c0ec5a86bbc6a0030"

UV_VERSION="0.13.0"
UV_URL="https://github.com/astral-sh/uv/releases/download/0.13.0/uv-x86_64-unknown-linux-gnu.tar.gz"
UV_SHA256="1468ebd5a5541121837c5a2817b9972ba6090fa6caa3d142620850a47fb75154"

ANSIBLE_CORE_VERSION="2.22.0"
ANSIBLE_LINT_VERSION="26.11.0"

log_info() { printf '[INFO] %s\n' "$*"; }
log_warn() { printf '[WARN] %s\n' "$*" >&2; }
log_error() { printf '[ERROR] %s\n' "$*" >&2; }
die() { log_error "$*"; exit 1; }

usage() {
  cat <<'EOF'
Usage: ./bootstrap.sh

Bootstraps v2 workstation tooling as your desktop user:
  - APT prerequisites (curl, git, gnupg, python3, age, ...)
  - uv (verified release tarball)
  - mise (verified release binary)
  - ansible-core via uv + required Ansible Galaxy collections
EOF
}

require_user() {
  local uid=${1:-$EUID}
  [[ $uid -ne 0 ]] || {
    log_error "Run as your desktop user, without sudo. Individual system operations use sudo."
    return 1
  }
}

require_supported_system() {
  local os_release=${1:-/etc/os-release}
  local arch=${2:-$(uname -m)}
  local os_id version major

  [[ -r "$os_release" ]] || { log_error "Missing OS release file: $os_release"; return 1; }
  os_id=$(awk -F= '$1 == "ID" {gsub(/"/, "", $2); print $2}' "$os_release") || return
  version=$(awk -F= '$1 == "VERSION_ID" {gsub(/"/, "", $2); print $2}' "$os_release") || return

  case "$os_id" in
    pop|ubuntu) ;;
    *) log_error "Supported systems: Pop!_OS or Ubuntu 24.04+; detected '$os_id' '$version'."; return 1 ;;
  esac

  major=${version%%.*}
  if [[ ! $major =~ ^[0-9]+$ ]] || (( 10#$major < 24 )); then
    log_error "Supported systems: Pop!_OS or Ubuntu 24.04+; detected $os_id $version."
    return 1
  fi

  [[ "$arch" == "x86_64" ]] || { log_error "v2 bootstrap requires x86_64; detected $arch."; return 1; }
}

verify_sha256() {
  local file=$1 expected=$2 actual
  [[ $expected =~ ^[a-fA-F0-9]{64}$ ]] || { log_error "Missing or invalid SHA-256 for $file"; return 1; }
  actual=$(sha256sum -- "$file") || return
  [[ ${actual%% *} == "${expected,,}" ]] || { log_error "Checksum mismatch: $file"; return 1; }
}

download_verified() {
  local url=$1 destination=$2 expected=$3 partial
  [[ $url == https://* ]] || { log_error "Downloads must use HTTPS: $url"; return 1; }
  mkdir -p -- "$(dirname "$destination")" || return
  partial=$(mktemp "${destination}.download.XXXXXX") || return
  if ! curl --proto '=https' --proto-redir '=https' --tlsv1.2 -fSL --retry 3 --connect-timeout 15 --max-time 600 "$url" -o "$partial"; then
    rm -f -- "$partial"
    return 1
  fi
  if ! verify_sha256 "$partial" "$expected"; then
    rm -f -- "$partial"
    return 1
  fi
  mv -T -- "$partial" "$destination"
}

install_apt_prereqs() {
  log_info "Installing APT bootstrap prerequisites..."
  sudo -v
  sudo apt-get update
  sudo apt-get install -y ca-certificates curl git gnupg tar xz-utils python3 python3-venv python3-yaml python3-debian age
}

install_uv() {
  local local_bin="$HOME/.local/bin"
  if command -v uv >/dev/null; then
    log_info "uv is already installed: $(command -v uv)"
    return 0
  fi
  log_info "Installing verified uv $UV_VERSION..."
  download_verified "$UV_URL" "$WORK_DIR/uv.tar.gz" "$UV_SHA256" || return
  python3 "$SCRIPT_DIR/scripts/st_assets.py" tar "$WORK_DIR/uv.tar.gz" "$WORK_DIR/uv" || return
  install -d -m 0755 "$local_bin" || return
  install -m 0755 "$WORK_DIR/uv/uv-x86_64-unknown-linux-gnu/uv" "$local_bin/uv" || return
  install -m 0755 "$WORK_DIR/uv/uv-x86_64-unknown-linux-gnu/uvx" "$local_bin/uvx" || return
}

install_mise() {
  local local_bin="$HOME/.local/bin"
  if command -v mise >/dev/null; then
    log_info "mise is already installed: $(command -v mise)"
  else
    log_info "Installing verified mise $MISE_VERSION..."
    download_verified "$MISE_URL" "$WORK_DIR/mise" "$MISE_SHA256" || return
    install -d -m 0755 "$local_bin" || return
    install -m 0755 "$WORK_DIR/mise" "$local_bin/mise" || return
  fi
  hash -r
}

install_ansible() {
  if ! command -v ansible-playbook >/dev/null; then
    log_info "Installing pinned ansible-core $ANSIBLE_CORE_VERSION via uv..."
    uv tool install --with-executables-from ansible-core "ansible-core==${ANSIBLE_CORE_VERSION}"
    hash -r
  else
    log_info "ansible-playbook is already installed: $(command -v ansible-playbook)"
  fi
  if ! command -v ansible-lint >/dev/null; then
    log_info "Installing pinned ansible-lint $ANSIBLE_LINT_VERSION via uv..."
    uv tool install "ansible-lint==${ANSIBLE_LINT_VERSION}"
    hash -r
  else
    log_info "ansible-lint is already installed: $(command -v ansible-lint)"
  fi
  log_info "Installing Ansible Galaxy collections..."
  ansible-galaxy collection install -r "$SCRIPT_DIR/ansible/requirements.yml"
}

trust_mise_config() {
  mise trust "$SCRIPT_DIR/mise.toml"
}

main() {
  case ${1:-} in
    -h|--help) usage; return 0 ;;
    '') [[ $# -eq 0 ]] || { usage >&2; die "Unexpected empty argument."; } ;;
    *) usage >&2; die "Unknown argument: $1" ;;
  esac

  require_user "$EUID"
  require_supported_system /etc/os-release "$(uname -m)"

  WORK_DIR=$(mktemp -d -t pop-bootstrap.XXXXXX) || die "Failed to create workspace."
  chmod 0700 "$WORK_DIR"
  trap 'rm -rf -- "$WORK_DIR"' EXIT

  mkdir -p "$HOME/.local/bin"
  export PATH="$HOME/.local/bin:$PATH"

  install_apt_prereqs
  install_uv
  install_mise
  install_ansible
  trust_mise_config

  log_info "Bootstrap complete."
  log_info "Next steps:"
  log_info "  mise run plan   # Dry-run Ansible playbook (--check --diff)"
  log_info "  mise run apply  # Converge workstation configuration"
  log_info "  mise run auth   # Interactive GitHub & Google Drive sign-in"
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
  main "$@"
fi
