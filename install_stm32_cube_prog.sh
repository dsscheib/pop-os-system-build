#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

main() {
  [[ $# -le 1 ]] || die "Usage: $0 [archive.zip]"
  st_preflight
  local archive payload icon=applications-development
  archive=$(find_archive "${1:-}" 'SetupSTM32CubeProgrammer_*.zip' 'SetupSTM32CubeProgrammer-*.zip')
  verify_installer "$archive"
  start_workspace cubeprogrammer
  python3 "$SCRIPT_DIR/scripts/st_assets.py" zip "$archive" "$WORK_DIR/archive"
  # The supplied Linux launcher loads the adjacent .exe, which is an IzPack JAR.
  # Invoke Java directly so paths are quoted and its failure status is preserved.
  payload=$(unique_file "$WORK_DIR/archive" 'SetupSTM32CubeProgrammer-*.exe')
  check_executable "$WORK_DIR/archive/jre/bin/java"
  begin_install "$HOME/STMicroelectronics/STM32Cube/STM32CubeProgrammer"
  python3 "$SCRIPT_DIR/scripts/st_assets.py" response "$SCRIPT_DIR/resources/izpack/cubeprogrammer.xml" "$INSTALL_DIR" "$WORK_DIR/response.xml" "$payload"
  # ST's post-install script copies ./jre into bin/jre. Preserve its expected
  # working directory without requiring a display for unattended setup.
  (
    cd -- "$(dirname "$payload")"
    "$WORK_DIR/archive/jre/bin/java" -Djava.awt.headless=true -jar "$payload" "$WORK_DIR/response.xml"
  )
  check_executable "$INSTALL_DIR/bin/STM32_Programmer_CLI"
  check_executable "$INSTALL_DIR/bin/STM32CubeProgrammerLauncher"
  check_executable "$INSTALL_DIR/bin/jre/bin/java"
  check_elf_dependencies "$INSTALL_DIR/bin/STM32_Programmer_CLI"
  if convert_icon_to_png "$INSTALL_DIR/util/Programmer.ico" "$INSTALL_DIR/launcher.png"; then icon="$INSTALL_DIR/launcher.png"; fi
  # GUI and CLI both execute from bin so their relative library paths work.
  st_finish STM32CubeProgrammer "$HOME/.local/bin/stm32cubeprogrammer" "$INSTALL_DIR/bin/STM32CubeProgrammerLauncher" "$INSTALL_DIR/bin" "$icon" com-st-stm32cube-programmer-STM32CubeProgrammer "$HOME/.local/share/applications/st-com-stm32cubeprogrammer.desktop"
  write_wrapper "$HOME/.local/bin/STM32_Programmer_CLI" "$INSTALL_DIR/bin" "$INSTALL_DIR/bin/STM32_Programmer_CLI"
  if [[ -x "$INSTALL_DIR/bin/STM32TrustedPackageCreator_CLI" ]]; then
    write_wrapper "$HOME/.local/bin/STM32TrustedPackageCreator_CLI" "$INSTALL_DIR/bin" "$INSTALL_DIR/bin/STM32TrustedPackageCreator_CLI"
  fi
  log_info "STM32CubeProgrammer GUI and CLI installed at $INSTALL_TARGET; previous versions are retained."
}
if [[ ${BASH_SOURCE[0]} == "$0" ]]; then main "$@"; fi
