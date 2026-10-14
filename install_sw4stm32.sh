#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

main() {
  [[ $# -le 1 ]] || die "Usage: $0 [installer.run]"
  st_preflight
  local archive java binary icon=applications-development
  archive=$(find_archive "${1:-}" 'install_sw4stm32_linux_64bits.run' 'sw4stm32_*.run')
  verify_installer "$archive"
  start_workspace sw4stm32
  python3 "$SCRIPT_DIR/scripts/st_assets.py" sw-jre "$archive" "$WORK_DIR/jre"
  java=$(unique_file "$WORK_DIR/jre" java)
  check_executable "$java"
  begin_install "$HOME/STMicroelectronics/sw4stm32"
  python3 "$SCRIPT_DIR/scripts/st_assets.py" response "$SCRIPT_DIR/resources/izpack/sw4stm32.xml" "$INSTALL_DIR" "$WORK_DIR/response.xml" "$archive"
  # The vendor wrapper masks Java's status with cleanup; use Java directly.
  "$java" -Djre.tmpdir="$(dirname "$(dirname "$java")")" -jar "$archive" "$WORK_DIR/response.xml"
  binary="$INSTALL_DIR/eclipse"
  check_executable "$binary"
  check_elf_dependencies "$binary"
  if convert_icon_to_png "$INSTALL_DIR/logo_openstm32.xpm" "$INSTALL_DIR/launcher.png"; then icon="$INSTALL_DIR/launcher.png"; fi
  st_finish 'System Workbench for STM32' "$HOME/.local/bin/sw4stm32" "$binary" "$INSTALL_DIR" "$icon" sw4stm32 "$HOME/.local/share/applications/sw4stm32-ide.desktop"
  log_info "System Workbench installed at $INSTALL_TARGET; previous versions are retained."
}
if [[ ${BASH_SOURCE[0]} == "$0" ]]; then main "$@"; fi
