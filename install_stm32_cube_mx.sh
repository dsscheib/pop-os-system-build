#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

remove_vendor_launcher() {
  local launcher="$HOME/.local/share/applications/com-st-microxplorer-maingui-STM32CubeMX.desktop"
  local expected_exec="Exec=$INSTALL_DIR/jre/bin/java -jar $INSTALL_DIR/STM32CubeMX"

  # IzPack writes this entry despite createMenuShortcuts=false. Remove only
  # its generated entry for the staging path we just installed.
  if [[ -f "$launcher" ]] && grep -Fqx -- "$expected_exec" "$launcher"; then
    rm -- "$launcher" || return
    refresh_desktop_environment "$(dirname "$launcher")"
  fi
}

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
  # ST's post-install script copies ./jre into the installation. Run from
  # the extracted archive, and avoid requiring a display for unattended setup.
  (
    cd -- "$(dirname "$installer")"
    "$WORK_DIR/archive/jre/bin/java" -Djava.awt.headless=true -jar "$installer" "$WORK_DIR/response.xml"
  )
  check_executable "$INSTALL_DIR/STM32CubeMX"
  check_executable "$INSTALL_DIR/jre/bin/java"
  # This is the 256x256 application icon used by ST's own launcher. The small
  # icon embedded in the installer JAR is only its generic installer window icon.
  if convert_icon_to_png "$INSTALL_DIR/help/STM32CubeMX.png" "$INSTALL_DIR/launcher.png"; then
    icon="$INSTALL_DIR/launcher.png"
  else
    log_warn "Native CubeMX application icon is unavailable; using the desktop theme icon."
  fi
  # ST's working launcher has no StartupWMClass. Let the desktop track the
  # started window through the launcher instead of guessing the Java window class.
  st_finish STM32CubeMX "$HOME/.local/bin/stm32cubemx" "$INSTALL_DIR/STM32CubeMX" "$INSTALL_DIR" "$icon" "" "$HOME/.local/share/applications/st-com-stm32cubemx.desktop"
  remove_vendor_launcher
  log_info "STM32CubeMX installed at $INSTALL_TARGET; previous versions are retained."
}
if [[ ${BASH_SOURCE[0]} == "$0" ]]; then main "$@"; fi
