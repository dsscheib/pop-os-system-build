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
  local env_file="${GDRIVE_OAUTH_ENV:-$HOME/.config/rclone/gdrive-oauth.env}" mode
  if [[ -f $env_file ]]; then
    mode=$(stat -c %a -- "$env_file")
    (( (8#$mode & 8#077) == 0 )) || {
      log_error "$env_file holds OAuth secrets but is readable by others (mode $mode); run: chmod 600 $env_file"
      return 1
    }
    # shellcheck source=/dev/null
    source "$env_file"
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
    rclone config update "$GDRIVE_REMOTE" "client_id=$GDRIVE_CLIENT_ID" "client_secret=$GDRIVE_CLIENT_SECRET" || return
    rclone config reconnect "$GDRIVE_REMOTE:"
    return
  fi

  if [[ ! -t 0 || ! -t 1 ]]; then
    log_warn "Google Drive is not authorized. Run 'mise run auth -- --step gdrive' in a terminal."
    return 1
  fi
  log_info "Complete Google Drive sign-in in the browser."
  rclone config create "$GDRIVE_REMOTE" drive scope=drive \
    "client_id=$GDRIVE_CLIENT_ID" "client_secret=$GDRIVE_CLIENT_SECRET" || return
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
  local baseline filters answer

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

  if systemctl --user list-unit-files rclone-gdrive-bisync.timer --no-legend >/dev/null 2>&1; then
    systemctl --user enable --now rclone-gdrive-bisync.timer || true
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
