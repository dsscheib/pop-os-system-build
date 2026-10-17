#!/usr/bin/env bash
# Configure Snapper and hibernation; run after booting the installed @ root.
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

verify_subvolumes() {
  [[ $(findmnt -n -o FSTYPE --mountpoint /) == btrfs ]] || die "Root must be Btrfs."
  [[ $(findmnt -n -o FSROOT --mountpoint /) == /@ ]] || die "Boot with rootflags=subvol=@ before configuring Snapper."
  [[ $(findmnt -n -o FSTYPE --mountpoint /.snapshots) == btrfs &&
     $(findmnt -n -o FSROOT --mountpoint /.snapshots) == /@snapshots &&
     $(findmnt -n -o UUID --mountpoint /.snapshots) == "$(findmnt -n -o UUID --mountpoint /)" ]] || die "Mount @snapshots at /.snapshots on the root filesystem first."
}

repair_old_hook() {
  # Earlier repository versions wrote invalid multiline APT syntax here.
  # Archive only our recognized file; do not move unrelated administrator config.
  if [[ -f /etc/apt/apt.conf.d/80snapper ]] && grep -q '^// Hardened Automatic Snapper snapshots' /etc/apt/apt.conf.d/80snapper; then
    local backup
    mkdir -p /etc/apt/pop-btrfs-backups
    backup=$(mktemp /etc/apt/pop-btrfs-backups/80snapper.XXXXXX)
    cp -a /etc/apt/apt.conf.d/80snapper "$backup"
    rm -f /etc/apt/apt.conf.d/80snapper
    log_info "Archived the old repository APT hook at $backup."
  fi
}

configure_snapper() {
  local template
  local -a options=()
  mkdir -p /etc/snapper/configs
  if [[ ! -f /etc/snapper/configs/root ]]; then
    for template in /etc/snapper/config-templates/default /usr/share/snapper/config-templates/default; do
      [[ -f "$template" ]] && break
    done
    [[ -f "$template" ]] || die "Snapper's default configuration template is missing."
    cp -a "$template" /etc/snapper/configs/root
    options+=(--initialize)
  fi
  python3 "$SCRIPT_DIR/scripts/configure_snapper.py" "${options[@]}"
  # Reload registration/config caches before D-Bus-backed snapper commands.
  systemctl restart snapperd.service
  snapper -c root set-config SYNC_ACL=yes
  chmod 0750 /.snapshots
  # Validate the registration and permissions with an actual snapshot operation.
  local number
  number=$(snapper -c root create -t single -c number -p -d 'Setup validation')
  [[ $number =~ ^[0-9]+$ ]] || die "Invalid validation snapshot number."
  snapper -c root list
  snapper -c root delete "$number"
}

setup_apt_snapshots() {
  # Parse in isolation so this test does not depend on unrelated host APT config.
  mkdir "$WORK_DIR/apt-parts"
  cp "$SCRIPT_DIR/resources/80pop-snapper" "$WORK_DIR/apt-parts/80pop-snapper"
  printf 'Dir::Etc::parts "%s";\nDir::Etc::main "/dev/null";\n' "$WORK_DIR/apt-parts" > "$WORK_DIR/apt-bootstrap"
  APT_CONFIG="$WORK_DIR/apt-bootstrap" apt-config dump >/dev/null
  install -D -o root -g root -m 0755 "$SCRIPT_DIR/scripts/snapper-apt-hook.sh" /usr/local/libexec/pop-snapper-apt-hook
  install -o root -g root -m 0644 "$SCRIPT_DIR/resources/80pop-snapper" /etc/apt/apt.conf.d/80pop-snapper
  apt-config dump >/dev/null
}

main() {
  local hibernation=1
  if [[ ${1:-} == --skip-hibernation ]]; then hibernation=0; shift; fi
  [[ $# -eq 0 ]] || die "Usage: sudo $0 [--skip-hibernation]"
  if [[ $EUID -ne 0 ]]; then
    if (( hibernation )); then exec sudo -- "$SCRIPT_DIR/${BASH_SOURCE[0]##*/}";
    else exec sudo -- "$SCRIPT_DIR/${BASH_SOURCE[0]##*/}" --skip-hibernation; fi
  fi
  require_commands findmnt python3 apt-get apt-config flock
  local os_id
  os_id=$(awk -F= '$1 == "ID" {gsub(/"/, "", $2); print $2}' /etc/os-release)
  case "$os_id" in pop|ubuntu|debian) ;; *) die "Snapper setup supports Pop!_OS, Ubuntu and Debian." ;; esac
  verify_subvolumes
  if (( hibernation )); then python3 "$SCRIPT_DIR/scripts/configure_hibernation.py" --check; fi
  start_workspace snapper
  repair_old_hook
  apt-get update
  apt-get install -y btrfs-progs python3-btrfsutil snapper snapper-gui libpam-snapper git acl
  local commit=04488f2350e8ec214277fe7de608492a9ee665a7
  git clone https://github.com/jrabinow/snapper-rollback.git "$WORK_DIR/rollback"
  git -C "$WORK_DIR/rollback" checkout --detach "$commit"
  [[ $(git -C "$WORK_DIR/rollback" rev-parse HEAD) == "$commit" ]] || die "Rollback commit verification failed."
  install -o root -g root -m 0755 "$WORK_DIR/rollback/snapper-rollback.py" /usr/local/bin/snapper-rollback
  local backup=""
  if [[ -f /etc/snapper-rollback.conf ]]; then
    backup=$(mktemp /etc/snapper-rollback.conf.backup.XXXXXX)
    cp -a /etc/snapper-rollback.conf "$backup"
  fi
  local config="$WORK_DIR/rollback.conf"
  printf '[root]\ndev = /dev/disk/by-uuid/%s\nsubvol_main = @\nsubvol_snapshots = @snapshots\nmountpoint = /btrfsroot\n' "$(findmnt -n -o UUID --mountpoint /)" > "$config"
  install -o root -g root -m 0644 "$config" /etc/snapper-rollback.conf
  if (( hibernation )); then python3 "$SCRIPT_DIR/scripts/configure_hibernation.py"; fi
  configure_snapper
  setup_apt_snapshots
  systemctl daemon-reload
  systemctl enable --now snapper-cleanup.timer
  if systemctl list-unit-files snapper-boot.timer --no-legend | grep -q '^snapper-boot.timer'; then
    systemctl enable --now snapper-boot.timer
  else log_warn "This distribution does not provide snapper-boot.timer; boot snapshots are unavailable."; fi
  log_info "Snapper root snapshots, rollback configuration and paired APT hooks are configured."
}
if [[ ${BASH_SOURCE[0]} == "$0" ]]; then main "$@"; fi
