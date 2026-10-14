#!/usr/bin/env bash
# Shared helpers. Callers own privilege policy and enable strict mode.

log_info() { printf '[INFO] %s\n' "$*"; }
log_warn() { printf '[WARN] %s\n' "$*" >&2; }
log_error() { printf '[ERROR] %s\n' "$*" >&2; }
die() { log_error "$*"; exit 1; }

require_commands() {
  local command_name
  for command_name in "$@"; do
    command -v "$command_name" >/dev/null || { log_error "Required command missing: $command_name"; return 1; }
  done
}

require_user() {
  [[ $EUID -ne 0 ]] || { log_error "Run as your desktop user, without sudo. Individual system operations use sudo."; return 1; }
}

require_supported_system() {
  local os_id version
  os_id=$(awk -F= '$1 == "ID" {gsub(/"/, "", $2); print $2}' /etc/os-release) || return
  version=$(awk -F= '$1 == "VERSION_ID" {gsub(/"/, "", $2); print $2}' /etc/os-release) || return
  case "$os_id:$version" in
    pop:22.04|pop:24.04|ubuntu:22.04|ubuntu:24.04) ;;
    *) log_error "Supported systems: Pop!_OS/Ubuntu 22.04 or 24.04; detected $os_id $version."; return 1 ;;
  esac
  [[ $(uname -m) == x86_64 ]] || { log_error "These workstation/STM32 installers require x86_64."; return 1; }
}

create_secure_tmpdir() {
  local tmp_dir
  tmp_dir=$(mktemp -d -t "${1:-build}.XXXXXX") || return
  [[ -n "$tmp_dir" && -d "$tmp_dir" ]] || return 1
  if ! chmod 0700 "$tmp_dir"; then
    rmdir -- "$tmp_dir" || log_warn "Could not remove unused temporary directory $tmp_dir"
    return 1
  fi
  printf '%s\n' "$tmp_dir"
}

start_workspace() {
  WORK_DIR=$(create_secure_tmpdir "$1") || return
  # All subsequent mktemp workspaces are children of this owned directory.
  export TMPDIR="$WORK_DIR"
  INSTALL_DIR=""
  INSTALL_COMMITTED=0
  trap finish_workspace EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
}

finish_workspace() {
  local status=$?
  trap - EXIT
  if [[ -n ${INSTALL_DIR:-} && ${INSTALL_COMMITTED:-0} == 0 ]]; then
    rm -rf -- "$INSTALL_DIR" || status=1
  fi
  if [[ -n ${WORK_DIR:-} ]]; then
    rm -rf -- "$WORK_DIR" || status=1
  fi
  exit "$status"
}

# Search each directory as a unit: patterns may overlap, but matches must be unique.
find_archive() {
  local explicit_path=$1 directory pattern match
  shift
  if [[ -n "$explicit_path" ]]; then
    [[ -f "$explicit_path" ]] || { log_error "Explicit installer does not exist: $explicit_path"; return 1; }
    realpath -- "$explicit_path"
    return
  fi
  local -a matches search_dirs=("$PWD" "${ST_DOWNLOAD_DIR:-$HOME/github/st-microelectronics}" "$HOME/Downloads")
  for directory in "${search_dirs[@]}"; do
    [[ -d "$directory" ]] || continue
    matches=()
    while IFS= read -r -d '' match; do
      for pattern in "$@"; do
        # shellcheck disable=SC2053 # Archive patterns intentionally contain globs.
        if [[ ${match##*/} == $pattern ]]; then matches+=("$match"); break; fi
      done
    done < <(find "$directory" -maxdepth 1 -type f -print0)
    if (( ${#matches[@]} > 1 )); then
      log_error "Multiple matching installers in $directory; pass an explicit path."
      return 1
    elif (( ${#matches[@]} == 1 )); then
      realpath -- "${matches[0]}"
      return
    fi
  done
  log_error "No matching installer found in the current directory, ST_DOWNLOAD_DIR, or ~/Downloads."
  return 1
}

verify_sha256() {
  local file=$1 expected=$2 actual
  [[ $expected =~ ^[a-fA-F0-9]{64}$ ]] || { log_error "Missing or invalid SHA-256 for $file"; return 1; }
  actual=$(sha256sum -- "$file") || return
  [[ ${actual%% *} == "${expected,,}" ]] || { log_error "Checksum mismatch: $file"; return 1; }
}

verify_installer() {
  local archive=$1 expected
  expected=${ST_INSTALLER_SHA256:-$(awk -v name="${archive##*/}" '$2 == name {print $1}' "$SCRIPT_DIR/resources/st-installers.sha256")}
  verify_sha256 "$archive" "$expected"
}

# Never expose partially downloaded files under their final names.
download_verified() {
  local url=$1 destination=$2 expected=$3 partial
  [[ $url == https://* ]] || { log_error "Downloads must use HTTPS."; return 1; }
  mkdir -p -- "$(dirname "$destination")" || return
  partial=$(mktemp "${destination}.download.XXXXXX") || return
  if ! curl --proto '=https' --proto-redir '=https' --tlsv1.2 -fSL --retry 3 --connect-timeout 15 --max-time 600 "$url" -o "$partial"; then
    rm -f -- "$partial"
    return 1
  fi
  if ! verify_sha256 "$partial" "$expected"; then rm -f -- "$partial"; return 1; fi
  mv -T -- "$partial" "$destination"
}

package_installed() {
  [[ $(dpkg-query -W -f='${Status}' "$1" 2>/dev/null) == 'install ok installed' ]]
}
install_deb() (
  local file=$1 package staging_dir status
  package=$(dpkg-deb -f "$file" Package) || return
  # _apt cannot traverse our private workspace. Stage only this package in a
  # root-owned directory; retain restrictive permissions on all other downloads.
  staging_dir=$(sudo mktemp -d /tmp/pop-apt.XXXXXX) || return
  [[ -n "$staging_dir" && -d "$staging_dir" ]] || { log_error "APT staging directory was not created."; return 1; }
  trap 'status=$?; trap - EXIT; sudo rm -f -- "$staging_dir/package.deb" || { (( status != 0 )) || status=1; }; sudo rmdir -- "$staging_dir" || { (( status != 0 )) || status=1; }; exit "$status"' EXIT
  sudo install -m 0644 -- "$file" "$staging_dir/package.deb" || return
  sudo chmod 0755 "$staging_dir" || return
  sudo apt-get install -y -- "$staging_dir/package.deb" || return
  package_installed "$package" || { log_error "Package is not installed: $package"; return 1; }
)

# IzPack embeds install paths in launchers. Keep each version at its original path
# and publish a stable symlink only after validation; retain the previous version.
begin_install() {
  local target=$1 parent base
  INSTALL_DIR="" INSTALL_COMMITTED=0
  parent=$(dirname "$target"); base=${target##*/}
  mkdir -p -- "$parent/.${base}.versions" || return
  INSTALL_DIR=$(mktemp -d "$parent/.${base}.versions/install.XXXXXX") || return
  chmod 0755 "$INSTALL_DIR" || return
  INSTALL_TARGET="$target"
}
publish_install() {
  local link backup=""
  link=$(mktemp "${INSTALL_TARGET}.link.XXXXXX") || return
  rm -f -- "$link" || return
  ln -s -- "$INSTALL_DIR" "$link" || return
  if [[ -e "$INSTALL_TARGET" && ! -L "$INSTALL_TARGET" ]]; then
    backup=$(mktemp -d "${INSTALL_TARGET}.backup.XXXXXX") || return
    rmdir -- "$backup" || return
    mv -T -- "$INSTALL_TARGET" "$backup" || return
  fi
  if ! mv -Tf -- "$link" "$INSTALL_TARGET"; then
    [[ -z "$backup" ]] || mv -T -- "$backup" "$INSTALL_TARGET"
    rm -f -- "$link"
    return 1
  fi
  INSTALL_COMMITTED=1
  [[ -z "$backup" ]] || log_info "Previous installation preserved at $backup"
}

check_executable() {
  [[ -f "$1" && -s "$1" && -x "$1" ]] || { log_error "Required executable missing or empty: $1"; return 1; }
}
check_elf_dependencies() {
  local output
  output=$(ldd "$1" 2>&1) || { log_error "$output"; return 1; }
  if [[ $output == *'not found'* ]]; then
    log_error "Missing runtime libraries for $1: $output"
    return 1
  fi
}

write_wrapper() {
  # Python quotes paths as shell literals, including spaces and metacharacters.
  python3 "$SCRIPT_DIR/scripts/st_assets.py" wrapper "$@"
}
write_desktop() {
  python3 "$SCRIPT_DIR/scripts/st_assets.py" desktop "$@"
}

convert_icon_to_png() {
  local src=$1 dest=$2
  [[ -f "$src" ]] || return 1
  mkdir -p -- "$(dirname "$dest")" || return
  if python3 - "$src" "$dest" <<'PY'
import sys
from PIL import Image, ImageOps
with Image.open(sys.argv[1]) as image:
    ImageOps.pad(image.convert("RGBA"), (256, 256), color=(0, 0, 0, 0)).save(sys.argv[2], "PNG")
PY
  then chmod 0644 "$dest"; else return 1; fi
}

# Install only the fixed, reviewed rules shipped in this repository. Installer
# directories and mutable remote rule files are never searched or trusted.
install_stlink_rules() {
  local rule
  for rule in "$SCRIPT_DIR"/resources/udev/49-stlink*.rules; do
    sudo install -o root -g root -m 0644 "$rule" /etc/udev/rules.d/ || return
  done
  sudo udevadm control --reload-rules || return
  log_info "ST-LINK rules installed. Reconnect the device to apply permissions."
}
refresh_desktop_environment() {
  local dir=${1:-$HOME/.local/share/applications}
  if command -v update-desktop-database >/dev/null; then
    update-desktop-database "$dir" || log_warn "Desktop database refresh failed."
  fi
  if command -v gtk-update-icon-cache >/dev/null && [[ -f "$HOME/.local/share/icons/hicolor/index.theme" ]]; then
    gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" || log_warn "Icon cache refresh failed."
  fi
}

unique_file() {
  local root=$1 pattern=$2 candidate
  local -a candidates=()
  while IFS= read -r -d '' candidate; do candidates+=("$candidate"); done < <(find "$root" -type f -name "$pattern" -print0)
  (( ${#candidates[@]} == 1 )) || { log_error "Expected one $pattern in $root, found ${#candidates[@]}."; return 1; }
  printf '%s\n' "${candidates[0]}"
}

st_preflight() {
  require_user || return
  require_supported_system || return
  require_commands python3 sha256sum realpath find mktemp sudo ldd desktop-file-validate || return
  python3 -c 'from PIL import Image' 2>/dev/null || log_warn "Pillow is unavailable; native launcher icons will be used when possible."
}

st_finish() {
  local name=$1 wrapper=$2 executable=$3 working_dir=$4 icon=$5 wmclass=$6 desktop_file=$7
  check_executable "$executable" || return
  write_wrapper "$WORK_DIR/wrapper" "$working_dir" "$executable" GDK_BACKEND=x11 _JAVA_AWT_WM_NONREPARENTING=1 || return
  write_desktop "$WORK_DIR/launcher.desktop" "$name" "$wrapper" "$working_dir" "$icon" "$wmclass" || return
  desktop-file-validate "$WORK_DIR/launcher.desktop" || return
  install_stlink_rules || return
  publish_install || return
  mkdir -p "$(dirname "$wrapper")" "$(dirname "$desktop_file")" || return
  # Regenerate with atomic replacement on each destination filesystem.
  write_wrapper "$wrapper" "$working_dir" "$executable" GDK_BACKEND=x11 _JAVA_AWT_WM_NONREPARENTING=1 || return
  write_desktop "$desktop_file" "$name" "$wrapper" "$working_dir" "$icon" "$wmclass" || return
  refresh_desktop_environment "$(dirname "$desktop_file")"
}

fetch_locked() {
  local name=$1 destination=$2 url digest
  url=$(jq -er --arg name "$name" '.[$name].url' "$SCRIPT_DIR/resources/downloads.json") || return
  digest=$(jq -er --arg name "$name" '.[$name].sha256' "$SCRIPT_DIR/resources/downloads.json") || return
  download_verified "$url" "$destination" "$digest"
}

install_system_file() {
  local source=$1 destination=$2 mode=${3:-0644} temporary
  sudo install -d -o root -g root -m 0755 "$(dirname "$destination")" || return
  temporary=$(sudo mktemp "${destination}.tmp.XXXXXX") || return
  if ! sudo install -o root -g root -m "$mode" "$source" "$temporary"; then
    sudo rm -f -- "$temporary"
    return 1
  fi
  sudo mv -Tf -- "$temporary" "$destination"
}
