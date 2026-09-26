#!/usr/bin/env bash
set -euo pipefail

# Target installation directory
TARGET_DIR="$HOME/STMicroelectronics/sw4stm32"
DESKTOP_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor/256x256/apps"
TEMP_DIR="/tmp/sw4stm32_extract"

# 1. Locate installer binary
INSTALLER=$(ls install_sw4stm32_linux_64bits.run sw4stm32_*.run 2>/dev/null | head -n 1 || true)

if [ -z "$INSTALLER" ]; then
  echo "Error: No 'install_sw4stm32_linux_64bits.run' file found in current directory."
  exit 1
fi

chmod +x "$INSTALLER"

# 2. Extract BitRock installer payload in user space
echo "==> Unpacking installer payload to $TARGET_DIR..."
mkdir -p "$TARGET_DIR" "$ICON_DIR" "$DESKTOP_DIR" "$TEMP_DIR"

# Execute installer in unattended mode targeting user space
./"$INSTALLER" --mode unattended --prefix "$TARGET_DIR" || true

# 3. Clean up generic or duplicated launchers created by BitRock
rm -f "$DESKTOP_DIR"/sw4stm32*.desktop \
      "$DESKTOP_DIR"/*SystemWorkbench*.desktop \
      "$HOME/Desktop"/sw4stm32*.desktop

# 4. Extract and register launcher icon
echo "==> Setting up application icon..."
ICON_SRC=$(find "$TARGET_DIR" -type f -name "icon.xpm" -o -name "logo.png" -o -name "icon.png" 2>/dev/null | head -n 1 || true)

if [ -n "$ICON_SRC" ] && command -v convert &>/dev/null && [[ "$ICON_SRC" == *.xpm ]]; then
  # Convert legacy XPM icon to high-res PNG if ImageMagick is available
  convert "$ICON_SRC" "$ICON_DIR/sw4stm32.png" 2>/dev/null || true
elif [ -n "$ICON_SRC" ]; then
  cp -f "$ICON_SRC" "$ICON_DIR/sw4stm32.png"
fi

# Fallback branding icon if local image extraction fails
if [ ! -f "$ICON_DIR/sw4stm32.png" ]; then
  curl -sSL "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e7/STMicroelectronics_logo.svg/512px-STMicroelectronics_logo.svg.png" -o "$ICON_DIR/sw4stm32.png" 2>/dev/null || true
fi

# 5. Create symlink in ~/.local/bin
mkdir -p "$HOME/.local/bin"
SW4_BIN=$(find "$TARGET_DIR" -maxdepth 2 -type f -name "sw4stm32" -o -name "eclipse" 2>/dev/null | head -n 1 || true)

if [ -n "$SW4_BIN" ]; then
  ln -sf "$SW4_BIN" "$HOME/.local/bin/sw4stm32"
fi

# 6. Install udev rules for ST-LINK if present inside installation payload
UDEV_RULE=$(find "$TARGET_DIR" -name "*stlink*.rules" 2>/dev/null | head -n 1 || true)
if [ -n "$UDEV_RULE" ]; then
  echo "==> Installing ST-LINK udev rules..."
  sudo cp "$UDEV_RULE" /etc/udev/rules.d/ 2>/dev/null || true
  sudo udevadm control --reload-rules 2>/dev/null || true
  sudo udevadm trigger 2>/dev/null || true
fi

# 7. Generate single clean .desktop launcher
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

# 8. Refresh caches & reload COSMIC launcher app library
gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
if command -v update-desktop-database &>/dev/null; then
  update-desktop-database "$DESKTOP_DIR" || true
fi
killall cosmic-app-library 2>/dev/null || true

echo "==> Installation complete!"
echo "==> Installed to $TARGET_DIR"
echo "==> Symlinked to $HOME/.local/bin/sw4stm32"
echo "==> Launcher created at $DESKTOP_DIR/st-com-sw4stm32.desktop"