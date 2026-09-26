#!/usr/bin/env bash
set -euo pipefail

# Target installation directory
TARGET_DIR="$HOME/STMicroelectronics/sw4stm32"
DESKTOP_DIR="$HOME/.local/share/applications"
SVG_ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"

# 1. Locate installer binary
INSTALLER=$(ls install_sw4stm32_linux_64bits.run sw4stm32_*.run 2>/dev/null | head -n 1 || true)

if [ -z "$INSTALLER" ]; then
  echo "Error: No 'install_sw4stm32_linux_64bits.run' file found in current directory."
  exit 1
fi

chmod +x "$INSTALLER"

# 2. Generate IzPack auto-install response XML
echo "==> Generating response configuration..."
mkdir -p "$TARGET_DIR" "$DESKTOP_DIR" "$SVG_ICON_DIR"

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

# 4. Target ALL variants of legacy sw4stm32 launchers safely
rm -f "$DESKTOP_DIR"/st-com-sw4stm32.desktop \
      "$DESKTOP_DIR"/sw4stm32*.desktop \
      "$DESKTOP_DIR"/*System*Workbench*.desktop \
      "$DESKTOP_DIR"/*system*workbench*.desktop \
      "$HOME/Desktop"/sw4stm32*.desktop

# 5. Generate scalable SVG logo for COSMIC compatibility
echo "==> Setting up scalable launcher icon..."
cat << 'EOF' > "$SVG_ICON_DIR/sw4stm32.svg"
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="256" height="256">
  <rect width="256" height="256" rx="40" fill="#00205B"/>
  <path d="M40 180 L128 40 L216 180 Z" fill="none" stroke="#00A3E0" stroke-width="18" stroke-linejoin="round"/>
  <circle cx="128" cy="130" r="28" fill="#00A3E0"/>
  <text x="128" y="222" font-family="sans-serif" font-weight="bold" font-size="28" fill="#FFFFFF" text-anchor="middle">STM32</text>
</svg>
EOF

chmod 644 "$SVG_ICON_DIR/sw4stm32.svg"

# 6. Locate main binary and create symlink in ~/.local/bin
mkdir -p "$HOME/.local/bin"
SW4_BIN=$(find "$TARGET_DIR" -maxdepth 2 -type f \( -name "sw4stm32" -o -name "eclipse" \) 2>/dev/null | head -n 1 || true)

if [ -z "$SW4_BIN" ]; then
  SW4_BIN="$TARGET_DIR/sw4stm32"
fi

ln -sf "$SW4_BIN" "$HOME/.local/bin/sw4stm32"

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
DESKTOP_FILE="$DESKTOP_DIR/sw4stm32-ide.desktop"

cat << EOF > "$DESKTOP_FILE"
[Desktop Entry]
Version=1.0
Type=Application
Name=System Workbench for STM32
Comment=Open Development Environment for STM32 (AC6 / SW4STM32)
Exec=$SW4_BIN %F
Path=$TARGET_DIR
Icon=sw4stm32
Terminal=false
Categories=Development;IDE;
StartupWMClass=Eclipse
EOF

chmod 755 "$DESKTOP_FILE"

# 9. Refresh databases & purge COSMIC app library index
echo "==> Updating desktop databases & restarting COSMIC components..."
gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
if command -v update-desktop-database &>/dev/null; then
  update-desktop-database "$DESKTOP_DIR" || true
fi

# Purge COSMIC local app grid cache & restart shell components
rm -rf "$HOME/.cache/cosmic" "$HOME/.cache/pop-launcher" 2>/dev/null || true
killall -9 cosmic-app-library pop-launcher cosmic-panel cosmic-applet-applications 2>/dev/null || true

echo "==> Installation complete!"
echo "==> Installed to $TARGET_DIR"
echo "==> Symlinked to $HOME/.local/bin/sw4stm32"
echo "==> Launcher created at $DESKTOP_FILE"
