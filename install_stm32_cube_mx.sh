#!/usr/bin/env bash
# ==============================================================================
# INSTALL STM32CubeMX
# Targets: Pop!_OS / Ubuntu x86_64
# ==============================================================================

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

TARGET_DIR="$HOME/STMicroelectronics/STM32Cube/STM32CubeMX"
DESKTOP_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor/256x256/apps"

# 1. Locate the downloaded zip archive (searches $1, current dir, and ~/Downloads)
EXPLICIT_ARG="${1:-}"
ARCHIVE_ZIP=$(find_archive "$EXPLICIT_ARG" "SetupSTM32CubeMX-*.zip" "en.stm32cubemx-*.zip" || true)

if [[ -z "$ARCHIVE_ZIP" || ! -f "$ARCHIVE_ZIP" ]]; then
  log_error "No 'SetupSTM32CubeMX-*.zip' or 'en.stm32cubemx-*.zip' found in current directory or ~/Downloads."
  log_error "Usage: $0 [path/to/SetupSTM32CubeMX-*.zip]"
  exit 1
fi

BUILD_TMP=$(create_secure_tmpdir "cubemx")
trap 'rm -rf "$BUILD_TMP"' EXIT

log_info "Extracting $ARCHIVE_ZIP to secure temporary directory..."
unzip -q -o "$ARCHIVE_ZIP" -d "$BUILD_TMP"

# 2. Identify the Linux installer binary
INSTALLER=$(find "$BUILD_TMP" -maxdepth 2 \( -name "SetupSTM32CubeMX-*.linux" -o -name "SetupSTM32CubeMX-*.bin" -o -name "SetupSTM32CubeMX*" \) ! -name "*.zip" -type f 2>/dev/null | head -n 1 || true)

if [[ -z "$INSTALLER" || ! -f "$INSTALLER" ]]; then
  log_error "SetupSTM32CubeMX installer binary not found after extraction."
  exit 1
fi

chmod +x "$INSTALLER"

# 3. Create the IzPack response file inside secure temporary directory
log_info "Generating IzPack silent installation response configuration..."
mkdir -p "$TARGET_DIR"

cat <<EOF >"$BUILD_TMP/auto-install.xml"
<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<AutomatedInstallation langpack="eng">
    <com.st.microxplorer.install.MXHTMLHelloPanel id="readme"/>
    <com.st.microxplorer.install.MXLicensePanel id="licence.panel"/>
    <com.st.microxplorer.install.MXAnalyticsPanel id="analytics.panel"/>
    <com.st.microxplorer.install.MXTargetPanel id="target.panel">
        <installpath>${TARGET_DIR}</installpath>
    </com.st.microxplorer.install.MXTargetPanel>
    <com.st.microxplorer.install.MXShortcutPanel id="shortcut.panel">
        <createMenuShortcuts>false</createMenuShortcuts>
        <createDesktopShortcuts>false</createDesktopShortcuts>
    </com.st.microxplorer.install.MXShortcutPanel>
    <com.st.microxplorer.install.MXInstallPanel id="install.panel"/>
    <com.st.microxplorer.install.MXFinishPanel id="finish.panel"/>
</AutomatedInstallation>
EOF

# 4. Execute the silent installer in user space
log_info "Installing STM32CubeMX silently to $TARGET_DIR..."
(
  cd "$BUILD_TMP"
  "$INSTALLER" "$BUILD_TMP/auto-install.xml"
)

# 5. Create wrapper in ~/.local/bin with Wayland / Java compatibility flags
mkdir -p "$HOME/.local/bin"
cat << 'EOF' > "$HOME/.local/bin/stm32cubemx"
#!/usr/bin/env bash
export GDK_BACKEND=x11
export _JAVA_AWT_WM_NONREPARENTING=1
cd "$HOME/STMicroelectronics/STM32Cube/STM32CubeMX" && ./STM32CubeMX "$@"
EOF
chmod +x "$HOME/.local/bin/stm32cubemx"

# 6. Create desktop launcher (.desktop) & set up icon
log_info "Setting up desktop launcher and icon..."
TMP_MX_ICON="$BUILD_TMP/mx_icon_extract"
mkdir -p "$ICON_DIR" "$DESKTOP_DIR" "$TMP_MX_ICON"

# Purge legacy launcher entries
rm -f "$DESKTOP_DIR"/st-com-stm32cubemx.desktop \
      "$DESKTOP_DIR"/STM32CubeMX*.desktop \
      "$DESKTOP_DIR"/*STM32CubeMX*.desktop \
      "$HOME/Desktop"/STM32CubeMX*.desktop 2>/dev/null || true

# Locate the primary application JAR and extract icons
MX_JAR=$(find "$TARGET_DIR" -type f -name "*.jar" ! -path "*/db/*" 2>/dev/null | head -n 1 || true)

if [[ -n "$MX_JAR" ]]; then
  unzip -q -o "$MX_JAR" "*icon*.png" "*MX*.png" "*logo*.png" -d "$TMP_MX_ICON" 2>/dev/null || true
  
  MX_EXTRACTED=$(find "$TMP_MX_ICON" -type f -name "*.png" -exec ls -s {} + 2>/dev/null | sort -nr | head -n 1 | awk '{print $2}' || true)
  if [[ -n "$MX_EXTRACTED" && -f "$MX_EXTRACTED" ]]; then
    convert_icon_to_png "$MX_EXTRACTED" "$ICON_DIR/stm32cubemx.png"
  fi
fi

# Fallback branding image if archive extraction fails
if [[ ! -f "$ICON_DIR/stm32cubemx.png" ]]; then
  curl -fsSL "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e7/STMicroelectronics_logo.svg/512px-STMicroelectronics_logo.svg.png" -o "$ICON_DIR/stm32cubemx.png" 2>/dev/null || true
fi

# Generate launcher adhering to Freedesktop standards
cat << EOF > "$DESKTOP_DIR/st-com-stm32cubemx.desktop"
[Desktop Entry]
Version=1.0
Type=Application
Name=STM32CubeMX
Comment=STMicroelectronics Code Generator
Exec=env GDK_BACKEND=x11 _JAVA_AWT_WM_NONREPARENTING=1 $TARGET_DIR/STM32CubeMX %F
Path=$TARGET_DIR
Icon=$ICON_DIR/stm32cubemx.png
Terminal=false
Categories=Development;IDE;
StartupWMClass=com-st-microxplorer-maingui-STM32CubeMX
EOF

chmod 755 "$DESKTOP_DIR/st-com-stm32cubemx.desktop"

# 7. Refresh desktop databases & reload COSMIC launcher
refresh_desktop_environment "$DESKTOP_DIR"

log_info "Success! STM32CubeMX installed to $TARGET_DIR"
log_info "Wrapper script created at $HOME/.local/bin/stm32cubemx"
log_info "Launcher created at $DESKTOP_DIR/st-com-stm32cubemx.desktop"
