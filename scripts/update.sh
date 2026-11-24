#!/usr/bin/env bash
# Keep this workstation current between `mise run apply` runs.
# Upgrades APT and Flatpak packages and Neovim and tmux plugins; reports
# firmware and Mise tool updates without applying them.
set -euo pipefail

ALL_STEPS=(apt flatpak firmware nvim tmux mise)

log_info() { printf '[INFO] %s\n' "$*"; }
log_warn() { printf '[WARN] %s\n' "$*" >&2; }
log_error() { printf '[ERROR] %s\n' "$*" >&2; }
die() { log_error "$*"; exit 2; }

usage() {
  cat <<'EOF'
Usage: mise run update [options]   (or: bash scripts/update.sh [options])

Upgrades, in order:
  apt       sudo apt-get update && sudo apt-get full-upgrade (shows the plan and asks first)
  flatpak   flatpak update (user and system installations)
  firmware  fwupdmgr get-updates (report only; apply with: fwupdmgr update)
  nvim      Lazy.nvim plugin sync (updates lazy-lock.json in ~/.config/nvim)
  tmux      TPM plugin update
  mise      mise outdated (report only; see --bump-mise)

Options:
  -y, --yes            Don't ask before APT upgrades
  --skip STEP[,STEP]   Skip these steps
  --only STEP[,STEP]   Run only these steps
  --bump-mise          Also run `mise upgrade --bump` and re-add the Mise config to Chezmoi
                       (you review and commit the dotfiles change)
  -h, --help           Show this help
EOF
}

ASSUME_YES=0
BUMP_MISE=0
requested=()
declare -A SELECTED=()
for step in "${ALL_STEPS[@]}"; do SELECTED[$step]=1; done

valid_steps() {
  local list=$1 step
  [[ -n $list ]] || die "Expected a comma-separated list of steps: ${ALL_STEPS[*]}"
  IFS=',' read -r -a requested <<<"$list"
  for step in "${requested[@]}"; do
    [[ -v SELECTED[$step] ]] || die "Unknown step: $step (expected: ${ALL_STEPS[*]})"
  done
}

parse_args() {
  local only="" skip="" step
  while (( $# > 0 )); do
    case $1 in
      -y|--yes) ASSUME_YES=1 ;;
      --bump-mise) BUMP_MISE=1 ;;
      --skip) [[ $# -ge 2 ]] || die "--skip needs a list of steps"; skip=$2; shift ;;
      --only) [[ $# -ge 2 ]] || die "--only needs a list of steps"; only=$2; shift ;;
      -h|--help) usage; exit 0 ;;
      *) usage >&2; die "Unknown argument: $1" ;;
    esac
    shift
  done
  if [[ -n $only ]]; then
    valid_steps "$only"
    for step in "${ALL_STEPS[@]}"; do SELECTED[$step]=0; done
    for step in "${requested[@]}"; do SELECTED[$step]=1; done
  fi
  if [[ -n $skip ]]; then
    valid_steps "$skip"
    for step in "${requested[@]}"; do SELECTED[$step]=0; done
  fi
}

# Each step returns 0 (done), 1 (failed) or 3 (skipped: tool or config absent).
# Steps chain commands with `|| return 1` because errexit is off inside `if`.

step_apt() {
  command -v apt-get >/dev/null || return 3
  local -a yes=()
  (( ASSUME_YES )) && yes=(-y)
  sudo apt-get update || return 1
  # full-upgrade also installs packages that plain `upgrade` holds back
  # (new dependencies, kernel and PPA package splits).
  sudo apt-get full-upgrade "${yes[@]}" || return 1
  if [[ -f /var/run/reboot-required ]]; then
    log_warn "A reboot is required to finish the APT upgrade."
  fi
}

step_flatpak() {
  command -v flatpak >/dev/null || return 3
  flatpak update -y || return 1
}

step_firmware() {
  command -v fwupdmgr >/dev/null || return 3
  local rc=0
  fwupdmgr refresh </dev/null >/dev/null 2>&1 || true
  fwupdmgr get-updates --no-unreported-check --no-metadata-check </dev/null || rc=$?
  # fwupdmgr exits 2 when there is nothing to update.
  case $rc in
    0) log_info "Firmware updates are listed above. Apply them with: fwupdmgr update" ;;
    2) log_info "No firmware updates." ;;
    *) return 1 ;;
  esac
}

step_nvim() {
  command -v nvim >/dev/null || return 3
  local config="$HOME/.config/nvim"
  [[ -f $config/lazy-lock.json || -d $HOME/.local/share/nvim/lazy/lazy.nvim ]] || return 3
  timeout --foreground 10m nvim --headless '+Lazy! sync' +qa || return 1
  if git -C "$config" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    if ! git -C "$config" diff --quiet -- lazy-lock.json; then
      log_info "Plugin versions changed in $config/lazy-lock.json; to roll back: git -C $config checkout lazy-lock.json && nvim --headless '+Lazy! restore' +qa"
    fi
  fi
}

step_tmux() {
  local tpm_dir="$HOME/.local/share/tmux/plugins"
  [[ -x $tpm_dir/tpm/bin/update_plugins ]] || return 3
  TMUX_PLUGIN_MANAGER_PATH="$tpm_dir" "$tpm_dir/tpm/bin/update_plugins" all || return 1
}

step_mise() {
  command -v mise >/dev/null || return 3
  local outdated
  # Plain `mise outdated` only looks inside each pinned version, so exact pins
  # never show. --bump lists every newer release. Run from $HOME so only the
  # global (dotfiles) config is considered, not a project's mise.toml.
  outdated=$(cd "$HOME" && mise outdated --bump 2>/dev/null) || return 1
  if [[ -z $outdated ]]; then
    log_info "All Mise tools are at their latest versions."
  else
    printf '%s\n' "$outdated"
    (( BUMP_MISE )) || log_info "Newer releases are available. Bump them with: mise run update --only mise --bump-mise (this can jump major versions; check the list first)"
  fi
  (( BUMP_MISE )) || return 0
  bump_mise_tools
}

bump_mise_tools() {
  local file
  local -a files=()
  (cd "$HOME" && mise upgrade --bump) || return 1
  if ! command -v chezmoi >/dev/null; then
    log_warn "chezmoi not found; ~/.config/mise changes are not saved to your dotfiles."
    return 0
  fi
  for file in "$HOME/.config/mise/config.toml" "$HOME/.config/mise/mise.lock"; do
    [[ -f $file ]] && chezmoi source-path "$file" >/dev/null 2>&1 && files+=("$file")
  done
  if (( ${#files[@]} == 0 )); then
    log_warn "Chezmoi doesn't manage ~/.config/mise; nothing to re-add."
    return 0
  fi
  chezmoi re-add "${files[@]}" || return 1
  chezmoi git -- status --short || true
  log_info "Review, then commit and push your dotfiles: chezmoi git -- commit -am 'Bump Mise tools' && chezmoi git -- push"
}

main() {
  parse_args "$@"
  [[ $EUID -ne 0 ]] || die "Run as your desktop user, without sudo; steps that need root use sudo."
  export PATH="$HOME/.local/bin:$HOME/.local/share/mise/shims:$PATH"

  local step rc failed=0
  local -a summary=()
  for step in "${ALL_STEPS[@]}"; do
    if (( ! SELECTED[$step] )); then
      summary+=("$step: skipped (not selected)")
      continue
    fi
    printf '\n==> %s\n' "$step"
    rc=0
    "step_$step" || rc=$?
    case $rc in
      0) summary+=("$step: ok") ;;
      3) summary+=("$step: skipped (not installed or not configured)") ;;
      *) summary+=("$step: FAILED"); failed=1 ;;
    esac
  done

  printf '\n==> Summary\n'
  printf '  %s\n' "${summary[@]}"
  if (( failed )); then
    log_error "Some steps failed; see the output above."
    return 1
  fi
  log_info "Pinned downloads in this repo aren't covered here; check them with: mise run pins"
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
  main "$@"
fi
