#!/usr/bin/env bash
# Interactive workstation authentication: GitHub CLI, Chezmoi dotfiles, rclone Google Drive, and Herdr hooks.
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
GDRIVE_REMOTE="gdrive"

log_info() { printf '[INFO] %s\n' "$*"; }
log_warn() { printf '[WARN] %s\n' "$*" >&2; }
log_error() { printf '[ERROR] %s\n' "$*" >&2; }
die() { log_error "$*"; exit 1; }

usage() {
  cat <<'EOF'
Usage: ./scripts/auth.sh [--step <all|github|chezmoi|gdrive|herdr>]

Runs interactive authentication and initial sync steps:
  github   Authenticate GitHub CLI with workflow scope
  chezmoi  Initialize or apply private Chezmoi dotfiles
  gdrive   Configure Google Drive rclone remote and initial bisync baseline
  herdr    Re-sync Herdr agent integrations for installed CLIs
EOF
}

local_var() {
  local key=$1 local_file="${ANSIBLE_LOCAL_VARS:-$REPO_ROOT/ansible/group_vars/all/local.yml}"
  [[ -f $local_file ]] || return 0
  python3 - "$local_file" "$key" <<'PY'
import sys
import yaml

data = yaml.safe_load(open(sys.argv[1], encoding='utf-8').read()) or {}
val = data.get(sys.argv[2], '')
if isinstance(val, str):
    print(val)
PY
}

auth_github() {
  if ! command -v gh >/dev/null; then
    log_warn "GitHub CLI (gh) is not installed; skipping GitHub sign-in."
    return 0
  fi
  if gh auth status --hostname github.com >/dev/null 2>&1; then
    log_info "GitHub CLI is already authenticated."
    return 0
  fi
  if [[ ! -t 0 || ! -t 1 ]]; then
    log_warn "Non-interactive shell; run 'gh auth login --hostname github.com --git-protocol https --scopes workflow --web' in a terminal."
    return 1
  fi
  log_info "Complete GitHub sign-in in the browser or with the device code."
  gh auth login --hostname github.com --git-protocol https --scopes workflow --web
}

auth_chezmoi() {
  local -a chezmoi_cmd
  if command -v chezmoi >/dev/null; then
    chezmoi_cmd=(chezmoi)
  elif command -v mise >/dev/null; then
    chezmoi_cmd=(mise exec chezmoi -- chezmoi)
  else
    log_info "Neither chezmoi nor mise is installed yet; skipping Chezmoi step."
    return 0
  fi
  local source_dir="$HOME/.local/share/chezmoi" repo
  if [[ -d "$source_dir/.git" ]]; then
    log_info "Applying existing Chezmoi dotfiles..."
    "${chezmoi_cmd[@]}" apply
    return 0
  fi
  repo=${DOTFILES_REPO:-$(local_var dotfiles_repo)}
  if [[ -z $repo || $repo == *your-username* ]]; then
    log_info "dotfiles_repo is unset; skipping Chezmoi init."
    return 0
  fi
  log_info "Initializing Chezmoi from $repo..."
  if command -v gh >/dev/null && gh auth status --hostname github.com >/dev/null 2>&1; then
    local index=${GIT_CONFIG_COUNT:-0}
    GIT_CONFIG_COUNT=$((index + 1)) \
      "GIT_CONFIG_KEY_$index=credential.https://github.com.helper" \
      "GIT_CONFIG_VALUE_$index=!gh auth git-credential" \
      "${chezmoi_cmd[@]}" init --apply "$repo"
  else
    "${chezmoi_cmd[@]}" init --apply "$repo"
  fi
}

load_gdrive_oauth_env() {
  local env_file="${GDRIVE_OAUTH_ENV:-$HOME/.config/rclone/gdrive-oauth.env}" mode key value pid
  if [[ -f $env_file ]]; then
    mode=$(stat -c %a -- "$env_file")
    (( (8#$mode & 8#077) == 0 )) || {
      log_error "$env_file holds OAuth secrets but is readable by others (mode $mode); run: chmod 600 $env_file"
      return 1
    }
    while IFS= read -r -d '' key && IFS= read -r -d '' value; do
      printf -v "$key" '%s' "$value"
    done < <(python3 - "$env_file" <<'PY'
import re
import sys

path = sys.argv[1]
with open(path, encoding='utf-8') as handle:
    for lineno, raw in enumerate(handle, 1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:].lstrip()
        key, sep, value = line.partition('=')
        key = key.strip()
        value = value.strip()
        if not sep or not re.fullmatch(r'GDRIVE_[A-Z0-9_]+', key):
            raise SystemExit(f'{path}:{lineno}: invalid OAuth env assignment (expected GDRIVE_*=VALUE)')
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        sys.stdout.buffer.write(key.encode('utf-8') + b'\0' + value.encode('utf-8') + b'\0')
PY
    )
    pid=$!
    wait "$pid" || return
  fi
}

gdrive_filters_file() {
  local candidate="${GDRIVE_FILTERS_FILE:-$HOME/.config/rclone/gdrive-filters.txt}"
  if [[ -n ${GDRIVE_FILTERS_FILE:-} && ! -f $GDRIVE_FILTERS_FILE ]]; then
    log_error "GDRIVE_FILTERS_FILE does not exist: $GDRIVE_FILTERS_FILE"
    return 1
  fi
  [[ -f $candidate ]] || return 0
  realpath -- "$candidate"
}

gdrive_baseline_id() {
  local filters digest=none local_dir="${GDRIVE_LOCAL_DIR:-$HOME/GoogleDrive}"
  filters=$(gdrive_filters_file) || return
  if [[ -n $filters ]]; then
    digest=$(sha256sum -- "$filters" | cut -d' ' -f1) || return
  fi
  printf '%s %s filters=%s\n' "$GDRIVE_REMOTE:${GDRIVE_REMOTE_PATH:-}" "$local_dir" "$digest"
}

gdrive_bisync_args() {
  local filters local_dir="${GDRIVE_LOCAL_DIR:-$HOME/GoogleDrive}"
  filters=$(gdrive_filters_file) || return
  printf '%s\n' bisync "$GDRIVE_REMOTE:${GDRIVE_REMOTE_PATH:-}" "$local_dir" \
    --create-empty-src-dirs --resilient --recover --max-lock 2m \
    --conflict-resolve newer --drive-skip-gdocs --verbose
  [[ -z $filters ]] || printf '%s\n' --filters-file "$filters"
}

rclone_config_path() {
  local out first_line path
  if command -v rclone >/dev/null 2>&1; then
    if out=$(rclone config file </dev/null 2>/dev/null); then
      first_line=${out%%$'\n'*}
      path=$(printf '%s\n' "$out" | awk 'NF { line = $0 } END { print line }')
      if [[ $first_line == "Configuration file "* && -n $path && $path != "$first_line" ]]; then
        printf '%s\n' "$path"
        return 0
      fi
    fi
  fi
  printf '%s\n' "${RCLONE_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/rclone/rclone.conf}"
}

write_gdrive_rclone_config() {
  local conf_path
  conf_path=$(rclone_config_path) || return
  GDRIVE_CLIENT_ID="$GDRIVE_CLIENT_ID" \
    GDRIVE_CLIENT_SECRET="$GDRIVE_CLIENT_SECRET" \
    GDRIVE_REMOTE="$GDRIVE_REMOTE" \
    RCLONE_CONF_PATH="$conf_path" \
    PYTHONPATH="$REPO_ROOT/scripts${PYTHONPATH:+:$PYTHONPATH}" \
    python3 - <<'PY'
import configparser
import io
import os
from pathlib import Path
from st_assets import atomic_text

path = Path(os.environ['RCLONE_CONF_PATH'])
if path.is_symlink():
    raise SystemExit(f'Refusing to write symlinked rclone config: {path}')
if path.exists() and not path.is_file():
    raise SystemExit(f'Refusing to write non-regular rclone config: {path}')
remote = os.environ['GDRIVE_REMOTE']
cfg = configparser.RawConfigParser()
cfg.optionxform = str
if path.exists():
    try:
        with path.open('r', encoding='utf-8') as handle:
            cfg.read_file(handle)
    except (OSError, UnicodeDecodeError, configparser.Error) as exc:
        raise SystemExit(f'Refusing to overwrite unreadable or unparseable rclone config {path}: {exc}') from exc
if not cfg.has_section(remote):
    cfg.add_section(remote)
cfg.set(remote, 'type', 'drive')
cfg.set(remote, 'scope', 'drive')
cfg.set(remote, 'client_id', os.environ['GDRIVE_CLIENT_ID'])
cfg.set(remote, 'client_secret', os.environ['GDRIVE_CLIENT_SECRET'])
cfg.remove_option(remote, 'token')
buf = io.StringIO()
cfg.write(buf)
atomic_text(path, buf.getvalue(), 0o600)
PY
}

switch_or_create_gdrive_remote() {
  local current
  if rclone listremotes | grep -Fxq "$GDRIVE_REMOTE:"; then
    current=$(rclone config dump | jq -r --arg remote "$GDRIVE_REMOTE" '.[$remote].client_id // ""') || return
    [[ $current != "$GDRIVE_CLIENT_ID" ]] || return 0
    if [[ ! -t 0 || ! -t 1 ]]; then
      log_warn "GDRIVE_CLIENT_ID differs from the $GDRIVE_REMOTE remote. Run 'mise run auth -- --step gdrive' in a terminal."
      return 1
    fi
    systemctl --user disable --now rclone-gdrive-bisync.timer >/dev/null 2>&1 || true
    log_info "Updating $GDRIVE_REMOTE OAuth client; complete sign-in in the browser."
    write_gdrive_rclone_config || return
    rclone config reconnect "$GDRIVE_REMOTE:"
    return
  fi

  if [[ ! -t 0 || ! -t 1 ]]; then
    log_warn "Google Drive is not authorized. Run 'mise run auth -- --step gdrive' in a terminal."
    return 1
  fi
  log_info "Complete Google Drive sign-in in the browser."
  write_gdrive_rclone_config || return
  rclone config reconnect "$GDRIVE_REMOTE:" || return
  rclone listremotes | grep -Fxq "$GDRIVE_REMOTE:" || {
    log_error "rclone did not save the $GDRIVE_REMOTE remote."
    return 1
  }
}

auth_gdrive() {
  if ! command -v rclone >/dev/null; then
    log_info "rclone is not installed yet; skipping Google Drive auth."
    return 0
  fi
  load_gdrive_oauth_env || return
  if [[ -z ${GDRIVE_CLIENT_ID:-} || -z ${GDRIVE_CLIENT_SECRET:-} ]]; then
    log_info "GDRIVE_CLIENT_ID / GDRIVE_CLIENT_SECRET not configured; skipping Google Drive auth."
    return 0
  fi

  local local_dir="${GDRIVE_LOCAL_DIR:-$HOME/GoogleDrive}"
  local state="${XDG_STATE_HOME:-$HOME/.local/state}/pop-workstation"
  local marker="$state/gdrive-bisync-initialized"
  local baseline filters answer unit_files

  mkdir -p "$local_dir" || return
  switch_or_create_gdrive_remote || return
  baseline=$(gdrive_baseline_id) || return

  if [[ ! -f $marker || $(<"$marker") != "$baseline" ]]; then
    systemctl --user disable --now rclone-gdrive-bisync.timer >/dev/null 2>&1 || true
    if [[ -t 0 && -t 1 ]]; then
      filters=$(gdrive_filters_file) || return
      log_info "Google Drive folder: $GDRIVE_REMOTE:${GDRIVE_REMOTE_PATH:-(all of My Drive)}"
      log_info "Local folder: $local_dir"
      log_info "Filters: ${filters:-none (everything in the Drive folder syncs)}"
      read -r -p 'Run the full Google Drive sync with these settings? [y/N] ' answer || return
      [[ $answer == [yY]* ]] || { log_warn "Google Drive resync skipped."; return 0; }
    fi
    local -a args
    mapfile -t args < <(gdrive_bisync_args) || return
    log_info "Running initial rclone bisync --resync..."
    rclone "${args[@]}" --resync --resync-mode newer || return
    mkdir -p "$state" || return
    printf '%s\n' "$baseline" > "$marker" || return
  fi

  unit_files=$(systemctl --user list-unit-files rclone-gdrive-bisync.timer --no-legend 2>/dev/null || true)
  if grep -Eq '^rclone-gdrive-bisync\.timer([[:space:]]|$)' <<<"$unit_files"; then
    systemctl --user enable --now rclone-gdrive-bisync.timer \
      || log_warn "Could not enable rclone-gdrive-bisync.timer."
  fi
  log_info "Google Drive remote is configured for $local_dir."
}

auth_herdr() {
  if ! command -v herdr >/dev/null; then
    return 0
  fi
  local entry cli target
  for entry in claude:claude codex:codex copilot:copilot agy:antigravity-cli; do
    cli=${entry%%:*}
    target=${entry#*:}
    command -v "$cli" >/dev/null || continue
    herdr integration install "$target" </dev/null || log_warn "Herdr $target integration reported an issue."
  done
}

main() {
  local step="all"
  while (( $# > 0 )); do
    case "$1" in
      --step)
        [[ $# -ge 2 && -n $2 ]] || die "Usage: $0 [--step <all|github|chezmoi|gdrive|herdr>]"
        step=$2
        shift 2
        ;;
      -h|--help)
        usage
        return 0
        ;;
      *)
        die "Unknown argument: $1"
        ;;
    esac
  done

  export PATH="$HOME/.local/bin:$HOME/.local/share/mise/shims:$PATH"
  case "$step" in
    all)
      auth_github
      auth_chezmoi
      auth_gdrive
      auth_herdr
      log_info "Sign in separately to AI CLIs as needed: agy, claude, codex, copilot (and ':Copilot auth' in Neovim)."
      ;;
    github) auth_github ;;
    chezmoi) auth_chezmoi ;;
    gdrive) auth_gdrive ;;
    herdr) auth_herdr ;;
    *) die "Unknown step: $step (expected all, github, chezmoi, gdrive, or herdr)" ;;
  esac
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
  main "$@"
fi
