#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

main() {
  [[ $# -le 1 ]] || die "Usage: $0 [archive.zip]"
  st_preflight
  local archive installer icon=applications-development
  archive=$(find_archive "${1:-}" 'SetupSTM32CubeMX-*.zip' 'en.stm32cubemx-*.zip')
  verify_installer "$archive"
  start_workspace cubemx
  python3 "$SCRIPT_DIR/scripts/st_assets.py" zip "$archive" "$WORK_DIR/archive"
  installer=$(unique_file "$WORK_DIR/archive" 'SetupSTM32CubeMX-*')
  check_executable "$WORK_DIR/archive/jre/bin/java"
  begin_install "$HOME/STMicroelectronics/STM32Cube/STM32CubeMX"
  python3 "$SCRIPT_DIR/scripts/st_assets.py" response "$SCRIPT_DIR/resources/izpack/cubemx.xml" "$INSTALL_DIR" "$WORK_DIR/response.xml" "$installer"
  "$WORK_DIR/archive/jre/bin/java" -jar "$installer" "$WORK_DIR/response.xml"
  check_executable "$INSTALL_DIR/STM32CubeMX"
  # Choose a known native resource; do not parse ls output or depend on the network.
  if python3 - "$installer" "$WORK_DIR/icon.png" <<'PY'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as archive:
    with open(sys.argv[2], 'wb') as stream:
        stream.write(archive.read('resources/JFrameIcon.png'))
PY
  then
    if convert_icon_to_png "$WORK_DIR/icon.png" "$INSTALL_DIR/launcher.png"; then icon="$INSTALL_DIR/launcher.png"; fi
  else log_warn "Native CubeMX icon is unavailable; using the desktop theme icon."; fi
  st_finish STM32CubeMX "$HOME/.local/bin/stm32cubemx" "$INSTALL_DIR/STM32CubeMX" "$INSTALL_DIR" "$icon" com-st-microxplorer-maingui-STM32CubeMX "$HOME/.local/share/applications/st-com-stm32cubemx.desktop"
  log_info "STM32CubeMX installed at $INSTALL_TARGET; previous versions are retained."
}
if [[ ${BASH_SOURCE[0]} == "$0" ]]; then main "$@"; fi
