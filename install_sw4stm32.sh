#!/usr/bin/env bash
set -euo pipefail

# Target installation directory
TARGET_DIR="$HOME/STMicroelectronics/sw4stm32"
DESKTOP_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor/256x256/apps"
TMP_SW4_ICON="/tmp/sw4_icon_extract"

# 1. Locate installer binary
INSTALLER=$(ls install_sw4stm32_linux_64bits.run sw4stm32_*.run 2>/dev/null | head -n 1 || true)

if [ -z "$INSTALLER" ]; then
  echo "Error: No 'install_sw4stm32_linux_64bits.run' file found in current directory."
  exit 1
fi

chmod +x "$INSTALLER"

# 2. Generate IzPack auto-install response XML
echo "==> Generating response configuration..."
mkdir -p "$TARGET_DIR" "$ICON_DIR" "$DESKTOP_DIR" "$TMP_SW4_ICON"

cat <<EOF >auto-install.xml
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
echo "==> Unpacking installer payload to $TARGET_DIR..."
./"$INSTALLER" auto-install.xml

# Cleanup temporary response file
rm -f auto-install.xml

# 4. Target ALL variants of sw4stm32 launchers to prevent duplication
rm -f "$DESKTOP_DIR"/st-com-sw4stm32.desktop \
      "$DESKTOP_DIR"/sw4stm32*.desktop \
      "$DESKTOP_DIR"/*[S|s]ystem*[W|w]orkbench*.desktop \
      "$HOME/Desktop"/sw4stm32*.desktop

# 5. Extract application PNG icon from installed plugins/JARs
echo "==> Setting up launcher icon..."
SW4_JAR=$(find "$TARGET_DIR" -type f -name "*.jar" ! -path "*/jre/*" 2>/dev/null | head -n 1 || true)

if [ -n "$SW4_JAR" ]; then
  unzip -q -o "$SW4_JAR" "*icon*.png" "*logo*.png" -d "$TMP_SW4_ICON" 2>/dev/null || true
  
  # Select the largest extracted PNG
  EXTRACTED_PNG=$(find "$TMP_SW4_ICON" -type f -name "*.png" -exec ls -s {} + 2>/dev/null | sort -nr | head -n 1 | awk '{print $2}' || true)
  if [ -n "$EXTRACTED_PNG" ]; then
    cp -f "$EXTRACTED_PNG" "$ICON_DIR/sw4stm32.png"
    echo "✔ Icon extracted and installed."
  fi
fi

rm -rf "$TMP_SW4_ICON"

# Fallback branding icon if extraction fails
if [ ! -f "$ICON_DIR/sw4stm32.png" ]; then
  curl -sSL "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e7/STMicroelectronics_logo.svg/512px-STMicroelectronics_logo.svg.png" -o "$ICON_DIR/sw4stm32.png" 2>/dev/null || true
fi

# 6. Locate main binary and create symlink in ~/.local/bin
mkdir -p "$HOME/.local/bin"
SW4_BIN=$(find "$TARGET_DIR" -maxdepth 2 -type f \( -name "sw4stm32" -o -name "eclipse" \) 2>/dev/null | head -n 1 || true)

if [ -n "$SW4_BIN" ]; then
  ln -sf "$SW4_BIN" "$HOME/.local/bin/sw4stm32"
fi

# 7. Install ST-LINK udev rules if included
UDEV_RULE=$(find "$TARGET_DIR" -name "*stlink*.rules" 2>/dev/null | head -n 1 || true)
if [ -n "$UDEV_RULE" ]; then
  echo "==> Installing ST-LINK udev rules..."
  sudo cp "$UDEV_RULE" /etc/udev/rules.d/ 2>/dev/null || true
  sudo udevadm control --reload-rules 2>/dev/null || true
  sudo udevadm trigger 2>/dev/null || true
fi

# 8. Create single clean .desktop launcher
echo "==> Creating .desktop launcher..."
cat << EOF > "$DESKTOP_DIR/st-com-sw4stm32.desktop"
[Desktop Entry]
Version=1.0
Type=Application
Name=System Workbench for STM32
Comment=Open Development Environment for STM32 (AC6 / SW4STM32)
Exec=$SW4_BIN %F
Path=$TARGET_DIR
Icon=$ICON_DIR/sw4stm32.png
Terminal=false
Categories=Development;IDE;
StartupWMClass=sw4stm32
EOF

chmod +x "$DESKTOP_DIR/st-com-sw4stm32.desktop"

# 9. Refresh icon cache & restart COSMIC app library
gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
if command -v update-desktop-database &>/dev/null; then
  update-desktop-database "$DESKTOP_DIR" || true
fi
killall cosmic-app-library 2>/dev/null || true

echo "==> Installation complete!"
echo "==> Installed to $TARGET_DIR"
echo "==> Symlinked to $HOME/.local/bin/sw4stm32"
echo "==> Launcher created at $DESKTOP_DIR/st-com-sw4stm32.desktop"
