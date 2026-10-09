#!/usr/bin/env bash
# ==============================================================================
# INSTALL System Workbench for STM32 (SW4STM32)
# Targets: Pop!_OS / Ubuntu x86_64
# ==============================================================================

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

TARGET_DIR="$HOME/STMicroelectronics/sw4stm32"
DESKTOP_DIR="$HOME/.local/share/applications"
HICOLOR_DIR="$HOME/.local/share/icons/hicolor/256x256/apps"

# 1. Locate installer binary (searches $1, current dir, and ~/Downloads)
EXPLICIT_ARG="${1:-}"
INSTALLER=$(find_archive "$EXPLICIT_ARG" "install_sw4stm32_linux_64bits.run" "sw4stm32_*.run" || true)

if [[ -z "$INSTALLER" || ! -f "$INSTALLER" ]]; then
  log_error "No 'install_sw4stm32_linux_64bits.run' or 'sw4stm32_*.run' found in current directory or ~/Downloads."
  log_error "Usage: $0 [path/to/install_sw4stm32_linux_64bits.run]"
  exit 1
fi

chmod +x "$INSTALLER"

BUILD_TMP=$(create_secure_tmpdir "sw4stm32")
trap 'rm -rf "$BUILD_TMP"' EXIT

# 2. Generate IzPack auto-install response XML inside secure temporary directory
log_info "Generating IzPack response configuration..."
mkdir -p "$TARGET_DIR" "$DESKTOP_DIR" "$HICOLOR_DIR"

cat <<EOF >"$BUILD_TMP/auto-install.xml"
<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<AutomatedInstallation langpack="eng">
    <com.izforge.izpack.panels.userinput.UserInputPanel id="warningPanel"/>
    <com.izforge.izpack.panels.info.InfoPanel id="InfoPanel_1"/>
    <com.izforge.izpack.panels.licence.LicencePanel id="SWLicence"/>
    <com.izforge.izpack.panels.licence.LicencePanel id="MPULicence"/>
    <com.izforge.izpack.panels.licence.LicencePanel id="JRELicence"/>
    <com.izforge.izpack.panels.target.TargetPanel id="TargetPanel_5">
        <installpath>${TARGET_DIR}</installpath>
    </com.izforge.izpack.panels.target.TargetPanel>
    <com.izforge.izpack.panels.packs.PacksPanel id="PacksPanel_6">
        <pack index="0" name="System Workbench for STM32" selected="true"/>
        <pack index="1" name="ST-Link/V2 driver" selected="true"/>
        <pack index="2" name="STLinkServer" selected="true"/>
    </com.izforge.izpack.panels.packs.PacksPanel>
    <com.izforge.izpack.panels.summary.SummaryPanel id="SummaryPanel_7"/>
    <com.izforge.izpack.panels.install.InstallPanel id="InstallPanel_8"/>
    <com.izforge.izpack.panels.process.ProcessPanel id="ProcessPanel_9"/>
    <com.izforge.izpack.panels.finish.FinishPanel id="FinishPanel_10"/>
</AutomatedInstallation>
EOF

# 3. Execute silent IzPack installation
log_info "Unpacking installer payload to $TARGET_DIR..."
"$INSTALLER" "$BUILD_TMP/auto-install.xml"

# 4. Target legacy sw4stm32 launchers safely
rm -f "$DESKTOP_DIR"/st-com-sw4stm32.desktop \
      "$DESKTOP_DIR"/sw4stm32*.desktop \
      "$DESKTOP_DIR"/*System*Workbench*.desktop \
      "$DESKTOP_DIR"/*system*workbench*.desktop \
      "$HOME/Desktop"/sw4stm32*.desktop 2>/dev/null || true

# 5. Convert native logo_openstm32 icon to PNG
log_info "Setting up launcher icon..."
ICON_DEST="$HICOLOR_DIR/sw4stm32.png"

SRC_ICON="$TARGET_DIR/logo_openstm32.xpm"
if [[ ! -f "$SRC_ICON" ]]; then
  SRC_ICON="$TARGET_DIR/logo_openstm32.ico"
fi
if [[ ! -f "$SRC_ICON" ]]; then
  SRC_ICON=$(find "$TARGET_DIR" -type f \( -name "logo_openstm32.xpm" -o -name "logo_openstm32.ico" \) 2>/dev/null | head -n 1 || true)
fi

if [[ -n "$SRC_ICON" && -f "$SRC_ICON" ]]; then
  convert_icon_to_png "$SRC_ICON" "$ICON_DEST"
else
  log_warn "Neither logo_openstm32.xpm nor logo_openstm32.ico found in $TARGET_DIR."
fi

# 6. Locate main binary and create symlink in ~/.local/bin
mkdir -p "$HOME/.local/bin"
SW4_BIN=$(find "$TARGET_DIR" -maxdepth 2 -type f \( -name "sw4stm32" -o -name "eclipse" \) 2>/dev/null | head -n 1 || true)

if [[ -z "$SW4_BIN" ]]; then
  SW4_BIN="$TARGET_DIR/sw4stm32"
fi

if [[ -f "$SW4_BIN" ]]; then
  ln -sf "$SW4_BIN" "$HOME/.local/bin/sw4stm32"
fi

# 7. Install ST-LINK udev rules securely if included
install_stlink_rules "$TARGET_DIR"

# 8. Create Freedesktop .desktop launcher
log_info "Creating desktop launcher..."
DESKTOP_FILE="$DESKTOP_DIR/sw4stm32-ide.desktop"

cat << EOF > "$DESKTOP_FILE"
[Desktop Entry]
Version=1.0
Type=Application
Name=System Workbench for STM32
Comment=Open Development Environment for STM32 (AC6 / SW4STM32)
Exec=$SW4_BIN %F
Path=$TARGET_DIR
Icon=$ICON_DEST
Terminal=false
Categories=Development;IDE;
StartupWMClass=sw4stm32
EOF

chmod 755 "$DESKTOP_FILE"

# 9. Refresh databases & reload COSMIC desktop state
refresh_desktop_environment "$DESKTOP_DIR"

log_info "Success! System Workbench installed to $TARGET_DIR"
log_info "Symlinked to $HOME/.local/bin/sw4stm32"
log_info "Launcher created at $DESKTOP_FILE"
