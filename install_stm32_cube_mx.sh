#!/usr/bin/env bash
set -euo pipefail

# Target installation directory
TARGET_DIR="$HOME/STMicroelectronics/STM32Cube/STM32CubeMX"
DESKTOP_DIR="$HOME/.local/share/applications"

# 1. Locate the downloaded zip archive
ARCHIVE_ZIP=$(ls SetupSTM32CubeMX-*.zip en.stm32cubemx-*.zip 2>/dev/null | head -n 1 || true)

if [ -z "$ARCHIVE_ZIP" ]; then
  echo "Error: No 'SetupSTM32CubeMX-*.zip' or 'en.stm32cubemx-*.zip' file found in current directory."
  exit 1
fi

echo "==> Extracting $ARCHIVE_ZIP..."
unzip -q -o "$ARCHIVE_ZIP"

# 2. Identify the Linux installer binary
INSTALLER=$(find . -maxdepth 2 \( -name "SetupSTM32CubeMX-*.linux" -o -name "SetupSTM32CubeMX-*.bin" -o -name "SetupSTM32CubeMX*" \) ! -name "*.zip" -type f 2>/dev/null | head -n 1 || true)

if [ -z "$INSTALLER" ]; then
  echo "Error: SetupSTM32CubeMX binary not found after extraction."
  exit 1
fi

chmod +x "$INSTALLER"

# 3. Create the IzPack response file (unquoted EOF allows ${TARGET_DIR} expansion)
echo "==> Generating response configuration..."
mkdir -p "$TARGET_DIR"

cat <<EOF >auto-install.xml
<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<AutomatedInstallation langpack="eng">
    <com.st.microxplorer.install.MXHTMLHelloPanel id="readme"/>
    <com.st.microxplorer.install.MXLicensePanel id="licence.panel"/>
    <com.st.microxplorer.install.MXAnalyticsPanel id="analytics.panel"/>
    <com.st.microxplorer.install.MXTargetPanel id="target.panel">
        <installpath>${TARGET_DIR}</installpath>
    </com.st.microxplorer.install.MXTargetPanel>
    <com.st.microxplorer.install.MXShortcutPanel id="shortcut.panel"/>
    <com.st.microxplorer.install.MXInstallPanel id="install.panel"/>
    <com.st.microxplorer.install.MXFinishPanel id="finish.panel"/>
</AutomatedInstallation>
EOF

# 4. Execute the silent installer in user space
echo "==> Installing STM32CubeMX silently..."
"$INSTALLER" auto-install.xml

# 5. Clean up temporary installer artifacts
rm -f "$INSTALLER" "$ARCHIVE_ZIP" auto-install.xml Readme.html
rm -rf jre

# 6. Create wrapper in ~/.local/bin to preserve current working directory for JAR execution
mkdir -p "$HOME/.local/bin"
cat << 'EOF' > "$HOME/.local/bin/stm32cubemx"
#!/usr/bin/env bash
cd "$HOME/STMicroelectronics/STM32Cube/STM32CubeMX" && ./STM32CubeMX "$@"
EOF
chmod +x "$HOME/.local/bin/stm32cubemx"

# 7. Create desktop launcher (.desktop) & set up icon
echo "==> Creating .desktop launcher..."
ICON_DIR="$HOME/.local/share/icons/hicolor/256x256/apps"
TMP_MX_ICON="/tmp/mx_icon_extract"

mkdir -p "$ICON_DIR" "$DESKTOP_DIR" "$TMP_MX_ICON"

# Target ONLY CubeMX launchers to prevent wiping other installed tools
rm -f "$DESKTOP_DIR"/st-com-stm32cubemx.desktop "$DESKTOP_DIR"/STM32CubeMX*.desktop

# Locate the primary application JAR (skipping database packs)
MX_JAR=$(find "$TARGET_DIR" -type f -name "*.jar" ! -path "*/db/*" 2>/dev/null | head -n 1 || true)

if [ -n "$MX_JAR" ]; then
  unzip -q -o "$MX_JAR" "*icon*.png" "*MX*.png" "*logo*.png" -d "$TMP_MX_ICON" 2>/dev/null || true
  
  # Select the largest extracted PNG image
  MX_EXTRACTED=$(find "$TMP_MX_ICON" -type f -name "*.png" -exec ls -s {} + 2>/dev/null | sort -nr | head -n 1 | awk '{print $2}' || true)
  if [ -n "$MX_EXTRACTED" ]; then
    cp -f "$MX_EXTRACTED" "$ICON_DIR/stm32cubemx.png"
    echo "✔ STM32CubeMX icon extracted and installed."
  fi
fi

rm -rf "$TMP_MX_ICON"

# Fallback branding image if archive extraction fails
if [ ! -f "$ICON_DIR/stm32cubemx.png" ]; then
  curl -sSL "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e7/STMicroelectronics_logo.svg/512px-STMicroelectronics_logo.svg.png" -o "$ICON_DIR/stm32cubemx.png" 2>/dev/null || true
fi

# Generate launcher with absolute icon path
cat << EOF > "$DESKTOP_DIR/st-com-stm32cubemx.desktop"
[Desktop Entry]
Version=1.0
Type=Application
Name=STM32CubeMX
Comment=STMicroelectronics Code Generator
Exec=$TARGET_DIR/STM32CubeMX %F
Path=$TARGET_DIR
Icon=$ICON_DIR/stm32cubemx.png
Terminal=false
Categories=Development;IDE;
StartupWMClass=com-st-microxplorer-maingui-STM32CubeMX
EOF

chmod +x "$DESKTOP_DIR/st-com-stm32cubemx.desktop"

# Refresh icon cache & restart COSMIC app library service
gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
if command -v update-desktop-database &>/dev/null; then
  update-desktop-database "$DESKTOP_DIR" || true
fi
killall cosmic-app-library 2>/dev/null || true

echo "==> Installation complete!"
echo "==> Wrapper script created at $HOME/.local/bin/stm32cubemx"
echo "==> Launcher created at $DESKTOP_DIR/st-com-stm32cubemx.desktop"
