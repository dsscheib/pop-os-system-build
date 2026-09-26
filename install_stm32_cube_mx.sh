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
rm -f auto-install.xml

# 6. Create wrapper in ~/.local/bin to preserve current working directory for JAR execution
mkdir -p "$HOME/.local/bin"
cat << 'EOF' > "$HOME/.local/bin/stm32cubemx"
#!/usr/bin/env bash
cd "$HOME/STMicroelectronics/STM32Cube/STM32CubeMX" && ./STM32CubeMX "$@"
EOF
chmod +x "$HOME/.local/bin/stm32cubemx"

# 7. Create desktop launcher (.desktop) & set up icon
echo "==> Creating .desktop launcher..."
mkdir -p "$DESKTOP_DIR"
mkdir -p "$HOME/.local/share/icons/hicolor/256x256/apps"

ICON_SRC=$(find "$TARGET_DIR" -type f \( -name "icon.png" -o -name "MX.png" -o -name "logo.png" \) 2>/dev/null | head -n 1 || true)

if [ -n "$ICON_SRC" ]; then
  cp "$ICON_SRC" "$HOME/.local/share/icons/hicolor/256x256/apps/stm32cubemx.png"
fi

cat << EOF > "$DESKTOP_DIR/st-com-stm32cubemx.desktop"
[Desktop Entry]
Version=1.0
Type=Application
Name=STM32CubeMX
Comment=STMicroelectronics Initialization Code Generator
Exec=$TARGET_DIR/STM32CubeMX %F
Path=$TARGET_DIR
Icon=stm32cubemx
Terminal=false
Categories=Development;IDE;
StartupWMClass=com-st-microxplorer-maingui-STM32CubeMX
EOF

chmod +x "$DESKTOP_DIR/st-com-stm32cubemx.desktop"

# Refresh icon cache & application list
gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
if command -v update-desktop-database &>/dev/null; then
  update-desktop-database "$DESKTOP_DIR" || true
fi

echo "==> Installation complete!"
echo "==> Wrapper script created at $HOME/.local/bin/stm32cubemx"
echo "==> Launcher created at $DESKTOP_DIR/st-com-stm32cubemx.desktop"
