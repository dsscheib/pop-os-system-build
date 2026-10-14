#!/usr/bin/env bash
# Install the verified CubeIDE payload as the desktop user; preserve old versions.
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

main() {
  [[ $# -le 1 ]] || die "Usage: $0 [archive.zip]"
  st_preflight
  require_commands dpkg-deb
  local archive installer package selected="" server="" binary icon
  archive=$(find_archive "${1:-}" 'stm32cubeide_*.sh.zip' 'stm32cubeide_*.zip')
  verify_installer "$archive"
  start_workspace stm32cubeide
  python3 "$SCRIPT_DIR/scripts/st_assets.py" zip "$archive" "$WORK_DIR/archive"
  installer=$(unique_file "$WORK_DIR/archive" 'stm32cubeide_*.sh')
  python3 "$SCRIPT_DIR/scripts/st_assets.py" makeself "$installer" "$WORK_DIR/payload"
  while IFS= read -r -d '' package; do
    case $(dpkg-deb -f "$package" Package) in
      stm32cubeide|stm32cubeide-*) [[ -z "$selected" ]] || die "Multiple CubeIDE packages."; selected=$package ;;
      st-stlink-server) [[ -z "$server" ]] || die "Multiple ST-LINK server packages."; server=$package ;;
    esac
  done < <(find "$WORK_DIR/payload" -type f -name '*.deb' -print0)
  [[ -n "$selected" && -n "$server" ]] || die "CubeIDE or its ST-LINK server package is missing."
  dpkg-deb --extract "$selected" "$WORK_DIR/unpacked"
  binary=$(unique_file "$WORK_DIR/unpacked" stm32cubeide)
  check_executable "$binary"
  begin_install "$HOME/STMicroelectronics/STM32Cube/stm32cubeide"
  cp -a -- "$(dirname "$binary")/." "$INSTALL_DIR/"
  dpkg-deb --extract "$server" "$WORK_DIR/server"
  cp -a -- "$WORK_DIR/server/usr/bin/stlink-server" "$INSTALL_DIR/stlink-server"
  check_executable "$INSTALL_DIR/stlink-server"
  check_elf_dependencies "$INSTALL_DIR/stm32cubeide"
  check_elf_dependencies "$INSTALL_DIR/stlink-server"
  icon="$INSTALL_DIR/icon.xpm"
  if [[ -f "$icon" ]] && convert_icon_to_png "$icon" "$INSTALL_DIR/launcher.png"; then icon="$INSTALL_DIR/launcher.png"; fi
  [[ -f "$icon" ]] || icon=applications-development
  st_finish STM32CubeIDE "$HOME/.local/bin/stm32cubeide" "$INSTALL_DIR/stm32cubeide" "$INSTALL_DIR" "$icon" stm32cubeide "$HOME/.local/share/applications/stm32cubeide.desktop"
  log_info "STM32CubeIDE installed at $INSTALL_TARGET; previous versions are retained."
}
if [[ ${BASH_SOURCE[0]} == "$0" ]]; then main "$@"; fi
