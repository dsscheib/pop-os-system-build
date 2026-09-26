#!/usr/bin/env bash
set -euo pipefail

# Standardized Paths
TARGET_DIR="$HOME/STMicroelectronics/STM32Cube/stm32cubeide"
DESKTOP_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor/256x256/apps"
BIN_DIR="$HOME/.local/bin"

# 1. Clean up old artifacts & extract archive
rm -f st-stm32cubeide_*.sh SetupSTM32CubeIDE-*.sh 2>/dev/null || true

ARCHIVE_ZIP=$(ls stm32cubeide_*.zip SetupSTM32CubeIDE_*.zip 2>/dev/null | head -n 1 || true)
if [ -z "$ARCHIVE_ZIP" ]; then
  echo "Error: No STM32CubeIDE zip archive found in current directory."
  exit 1
fi

echo "==> Extracting $ARCHIVE_ZIP..."
unzip -q -o "$ARCHIVE_ZIP"

# 2. Locate Linux Installer Payload
INSTALLER=$(find . -maxdepth 2 \( -name "st-stm32cubeide_*.sh" -o -name "SetupSTM32CubeIDE-*.sh" \) ! -name "*.exe" ! -name "*.zip" -type f 2>/dev/null | head -n 1 || true)
if [ -z "$INSTALLER" ]; then
  echo "Error: STM32CubeIDE shell installer not found."
  exit 1
fi

chmod +x "$INSTALLER"

# 3. Silent Installation
echo "==> Installing STM32CubeIDE silently..."
mkdir -p "$TARGET_DIR"
# ST shell installers support unattended extraction with standard options
./"$INSTALLER" --mode silent --prefix "$TARGET_DIR" 2>/dev/null || ./"$INSTALLER" -y -q -p "$TARGET_DIR"

# 4. Cleanup extracted setup payload
rm -f "$INSTALLER"

# 5. Binaries & CLI Symlinks
mkdir -p "$BIN_DIR"
ln -sf "$TARGET_DIR/stm32cubeide" "$BIN_DIR/stm32cubeide"

# 6. Install udev rules if present
UDEV_RULE=$(find "$TARGET_DIR" -name "*stlink*.rules" 2>/dev/null | head -n 1 || true)
if [ -n "$UDEV_RULE" ]; then
  echo "==> Installing ST-LINK udev rules..."
  sudo cp "$UDEV_RULE" /etc/udev/rules.d/
  sudo udevadm control --reload-rules || true
  sudo udevadm trigger || true
fi

# 7. Icon Provisioning (Direct 256px resolution target)
mkdir -p "$ICON_DIR"
IDE_256=$(find "$TARGET_DIR" -type f -name "STM32CubeIDE_icon_256px.png" 2>/dev/null | head -n 1 || true)

if [ -n "$IDE_256" ]; then
  cp -f "$IDE_256" "$ICON_DIR/stm32cubeide.png"
else
  cp -f "$TARGET_DIR/icon.xpm" "$ICON_DIR/stm32cubeide.png" 2>/dev/null || true
fi

# 8. Clean duplicates & write .desktop Launcher
mkdir -p "$DESKTOP_DIR"
rm -f "$DESKTOP_DIR"/STM32*.desktop "$DESKTOP_DIR"/st-com-stm32cubeide.desktop

cat << EOF > "$DESKTOP_DIR/st-com-stm32cubeide.desktop"
[Desktop Entry]
Version=1.0
Type=Application
Name=STM32CubeIDE
Comment=STMicroelectronics Integrated Development Environment for STM32
Exec=$TARGET_DIR/stm32cubeide %F
Path=$TARGET_DIR
Icon=$ICON_DIR/stm32cubeide.png
Terminal=false
Categories=Development;IDE;
StartupWMClass=stm32cubeide
EOF

chmod +x "$DESKTOP_DIR/st-com-stm32cubeide.desktop"

# 9. Cache & COSMIC Service Refresh
gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
if command -v update-desktop-database &>/dev/null; then
  update-desktop-database "$DESKTOP_DIR" || true
fi
killall cosmic-app-library 2>/dev/null || true

echo "==> STM32CubeIDE installation complete!"
