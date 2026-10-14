#!/usr/bin/env bash
# APT invokes this as root. Snapshot failures are reported but do not block APT.
set -euo pipefail
warn() { printf '[Snapper APT] %s\n' "$*" >&2; }

snapshot_pair() {
  local action=$1 state_dir=$2 number temporary
  exec 9>"$state_dir/lock"
  flock -x 9
  if [[ $action == pre ]]; then
    rm -f -- "$state_dir/apt-pre.num"
    if number=$(snapper -c root create -t pre -c number -p -d 'APT Pre-Update Snapshot'); then
      if [[ $number =~ ^[0-9]+$ ]]; then
        temporary=$(mktemp "$state_dir/pre.XXXXXX")
        printf '%s\n' "$number" > "$temporary"
        mv -T -- "$temporary" "$state_dir/apt-pre.num"
      else warn 'Snapper returned an invalid snapshot number.'; fi
    else warn 'Pre-snapshot failed; APT will continue.'; fi
  elif [[ -f "$state_dir/apt-pre.num" && ! -L "$state_dir/apt-pre.num" ]]; then
    number=$(cat "$state_dir/apt-pre.num")
    if [[ $number =~ ^[0-9]+$ ]]; then
      snapper -c root create -t post -c number --pre-number="$number" -d 'APT Post-Update Snapshot' || warn 'Post-snapshot failed; APT will continue.'
    else warn 'Ignoring invalid pre-snapshot state.'; fi
    rm -f -- "$state_dir/apt-pre.num"
  fi
}

main() {
  [[ ${1:-} == pre || ${1:-} == post ]] || { warn 'Expected pre or post'; return 1; }
  [[ $EUID -eq 0 ]] || { warn 'Root privileges are required'; return 1; }
  if ! command -v snapper >/dev/null || [[ ! -f /etc/snapper/configs/root ]]; then
    warn 'Root snapshot configuration is unavailable; skipping snapshot.'
    return 0
  fi
  if [[ $(findmnt -n -o FSROOT --mountpoint /) != /@ ||
        $(findmnt -n -o FSROOT --mountpoint /.snapshots) != /@snapshots ||
        $(findmnt -n -o UUID --mountpoint /) != "$(findmnt -n -o UUID --mountpoint /.snapshots)" ]]; then
    warn 'Expected root/snapshot mounts are unavailable; skipping snapshot.'
    return 0
  fi
  local state_dir=/run/pop-snapper
  [[ ! -L "$state_dir" ]] || { warn 'Refusing symlinked state directory'; return 1; }
  install -d -o root -g root -m 0700 "$state_dir"
  snapshot_pair "$1" "$state_dir"
}
if [[ ${BASH_SOURCE[0]} == "$0" ]]; then main "$@"; fi
