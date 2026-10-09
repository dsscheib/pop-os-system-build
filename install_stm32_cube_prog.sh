#!/usr/bin/env bash
# ==============================================================================
# INSTALL STM32CubeProgrammer (STM32CubeProg)
# Targets: Pop!_OS / Ubuntu x86_64
# ==============================================================================

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

TARGET_DIR="$HOME/STMicroelectronics/STM32Cube/STM32CubeProgrammer"
DESKTOP_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor/256x256/apps"

# 1. Locate the downloaded zip archive (searches $1, current dir, and ~/Downloads)
EXPLICIT_ARG="${1:-}"
ARCHIVE_ZIP=$(find_archive "$EXPLICIT_ARG" "SetupSTM32CubeProgrammer_*.zip" "SetupSTM32CubeProgrammer-*.zip" || true)

if [[ -z "$ARCHIVE_ZIP" || ! -f "$ARCHIVE_ZIP" ]]; then
  log_error "No 'SetupSTM32CubeProgrammer_*.zip' found in current directory or ~/Downloads."
  log_error "Usage: $0 [path/to/SetupSTM32CubeProgrammer_*.zip]"
  exit 1
fi

BUILD_TMP=$(create_secure_tmpdir "cubeprog")
trap 'rm -rf "$BUILD_TMP"' EXIT

log_info "Extracting $ARCHIVE_ZIP into secure temporary directory..."
unzip -q -o "$ARCHIVE_ZIP" -d "$BUILD_TMP"

# 2. Identify the Linux installer binary
INSTALLER=$(find "$BUILD_TMP" -maxdepth 2 \( -name "SetupSTM32CubeProgrammer-*.linux" -o -name "SetupSTM32CubeProgrammer-*.bin" -o -name "*.linux" \) ! -name "*.exe" ! -name "*.zip" -type f 2>/dev/null | head -n 1 || true)

if [[ -z "$INSTALLER" || ! -f "$INSTALLER" ]]; then
  log_error "SetupSTM32CubeProgrammer Linux binary not found after extraction."
  exit 1
fi

chmod +x "$INSTALLER"

# 3. Generate response configuration in secure temporary directory
log_info "Generating IzPack response configuration..."
mkdir -p "$TARGET_DIR"

cat <<EOF >"$BUILD_TMP/installer.auto"
<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<AutomatedInstallation langpack="eng">
<com.st.CustomPanels.CheckedHelloPorgrammerPanel id="Hello.panel"/>
<com.izforge.izpack.panels.info.InfoPanel id="Info.panel"/>
<com.izforge.izpack.panels.licence.LicencePanel id="Licence.panel"/>
<com.st.CustomPanels.TargetProgrammerPanel id="target.panel">
<installpath>${TARGET_DIR}</installpath>
</com.st.CustomPanels.TargetProgrammerPanel>
<com.st.CustomPanels.AnalyticsPanel id="analytics.panel"/>
<com.st.CustomPanels.PacksProgrammerPanel id="Packs.panel">
<pack index="0" name="Core Files" selected="true"/>
<pack index="1" name="STM32CubeProgrammer" selected="true"/>
<pack index="2" name="STM32TrustedPackageCreator" selected="true"/>
</com.st.CustomPanels.PacksProgrammerPanel>
<com.izforge.izpack.panels.install.InstallPanel id="Install.panel"/>
<com.izforge.izpack.panels.shortcut.ShortcutPanel id="Shortcut.panel">
<createMenuShortcuts>false</createMenuShortcuts>
<programGroup>STMicroelectronics\STM32CubeProgrammer</programGroup>
<createDesktopShortcuts>false</createDesktopShortcuts>
<createStartupShortcuts>false</createStartupShortcuts>
<shortcutType>user</shortcutType>
</com.izforge.izpack.panels.shortcut.ShortcutPanel>
<com.st.CustomPanels.FinishProgrammerPanel id="finish.panel"/>
</AutomatedInstallation>
EOF

# 4. Execute silent installation
log_info "Installing STM32CubeProgrammer silently to $TARGET_DIR..."
(
  cd "$BUILD_TMP"
  "$INSTALLER" -f "$BUILD_TMP/installer.auto"
)

# 5. Add CLI binaries and GUI wrapper to ~/.local/bin
mkdir -p "$HOME/.local/bin"

if [[ -d "$TARGET_DIR/bin" ]]; then
  ln -sf "$TARGET_DIR/bin/STM32_Programmer_CLI" "$HOME/.local/bin/STM32_Programmer_CLI"
  
  if [[ -f "$TARGET_DIR/bin/STM32TrustedPackageCreator_CLI" ]]; then
    ln -sf "$TARGET_DIR/bin/STM32TrustedPackageCreator_CLI" "$HOME/.local/bin/STM32TrustedPackageCreator_CLI"
  fi
fi

# GUI wrapper script with Java Wayland/GTK compatibility flags
cat << 'EOF' > "$HOME/.local/bin/stm32cubeprogrammer"
#!/usr/bin/env bash
export GDK_BACKEND=x11
export _JAVA_OPTIONS="-Djdk.gtk.version=2"
export _JAVA_AWT_WM_NONREPARENTING=1
cd "$HOME/STMicroelectronics/STM32Cube/STM32CubeProgrammer/bin" && ./STM32CubeProgrammerLauncher "$@"
EOF
chmod +x "$HOME/.local/bin/stm32cubeprogrammer"

# 6. Install ST-LINK udev rules securely if present
install_stlink_rules "$TARGET_DIR"

# 7. Setup launcher icon using native Programmer.ico
log_info "Setting up launcher icon..."
mkdir -p "$ICON_DIR" "$DESKTOP_DIR"
rm -f "$DESKTOP_DIR"/st-com-stm32cubeprogrammer.desktop "$DESKTOP_DIR"/STM32CubeProgrammer*.desktop 2>/dev/null || true

ICO_SRC="$TARGET_DIR/util/Programmer.ico"
ICON_DEST="$ICON_DIR/stm32cubeprogrammer.png"

if [[ ! -f "$ICO_SRC" ]]; then
  ICO_SRC=$(find "$TARGET_DIR" -type f -iname "Programmer.ico" 2>/dev/null | head -n 1 || true)
fi

if [[ -n "$ICO_SRC" && -f "$ICO_SRC" ]]; then
  convert_icon_to_png "$ICO_SRC" "$ICON_DEST"
else
  log_warn "Programmer.ico not found in $TARGET_DIR."
fi

# 8. Generate Freedesktop .desktop launcher
DESKTOP_FILE="$DESKTOP_DIR/st-com-stm32cubeprogrammer.desktop"

cat << EOF > "$DESKTOP_FILE"
[Desktop Entry]
Version=1.0
Type=Application
Name=STM32CubeProg
Comment=STMicroelectronics Flash Programming Tool for STM32
Exec=env GDK_BACKEND=x11 _JAVA_OPTIONS="-Djdk.gtk.version=2" _JAVA_AWT_WM_NONREPARENTING=1 $TARGET_DIR/bin/STM32CubeProgrammerLauncher %F
Path=$TARGET_DIR/bin
Icon=$ICON_DEST
Terminal=false
Categories=Development;IDE;
StartupWMClass=com-st-stm32cube-programmer-STM32CubeProgrammer
EOF

chmod 755 "$DESKTOP_FILE"

# 9. Refresh desktop databases & reload COSMIC launcher
refresh_desktop_environment "$DESKTOP_DIR"

log_info "Success! STM32CubeProgrammer installed to $TARGET_DIR"
log_info "CLI linked to $HOME/.local/bin/STM32_Programmer_CLI"
log_info "GUI wrapper created at $HOME/.local/bin/stm32cubeprogrammer"
log_info "Launcher created at $DESKTOP_FILE"
