#!/usr/bin/env bash
set -euo pipefail

# Target installation directory
IDE_TARGET_DIR="$HOME/STMicroelectronics/STM32Cube/stm32cubeide"
TEMP_DIR="/tmp/stm32cubeide_extract"
DESKTOP_DIR="$HOME/.local/share/applications"
HICOLOR_DIR="$HOME/.local/share/icons/hicolor/256x256/apps"
TMP_IDE_ICON="/tmp/ide_icon_extract"

# 1. Locate the downloaded zip archive
ARCHIVE_ZIP=$(ls stm32cubeide_*.sh.zip stm32cubeide_*.zip 2>/dev/null | head -n 1 || true)

if [ -z "$ARCHIVE_ZIP" ]; then
  echo "Error: No 'stm32cubeide_*.zip' file found in current directory."
  exit 1
fi

echo "==> Unzipping $ARCHIVE_ZIP..."
unzip -q -o "$ARCHIVE_ZIP"

# 2. Locate shell installer script
INSTALLER=$(ls stm32cubeide_*.sh 2>/dev/null | head -n 1 || true)

if [ -z "$INSTALLER" ]; then
  echo "Error: STM32CubeIDE shell installer (.sh) not found after extraction."
  exit 1
fi

chmod +x "$INSTALLER"

# 3. Unpack installer payload to temp folder
echo "==> Unpacking makeself payload..."
rm -rf "$TEMP_DIR"
./"$INSTALLER" --noexec --target "$TEMP_DIR"

# 4. Extract binaries directly without dpkg / apt validation
echo "==> Preparing target directory $IDE_TARGET_DIR..."
rm -rf "$IDE_TARGET_DIR"
mkdir -p "$IDE_TARGET_DIR"

DEB_FILE=$(find "$TEMP_DIR" -name "*.deb" | head -n 1 || true)

if [ -n "$DEB_FILE" ]; then
  WORKDIR="/tmp/deb_unpack"
  rm -rf "$WORKDIR" && mkdir -p "$WORKDIR"
  
  ar x "$DEB_FILE" --output="$WORKDIR"
  
  TAR_DATA=$(find "$WORKDIR" -name "data.tar.*" | head -n 1)
  tar -xf "$TAR_DATA" -C "$WORKDIR"
  
  INTERNAL_DIR=$(find "$WORKDIR" -type d -name "stm32cubeide_*" | head -n 1 || true)
  if [ -z "$INTERNAL_DIR" ]; then
    INTERNAL_DIR=$(find "$WORKDIR" -type f -name "stm32cubeide" -exec dirname {} \; | head -n 1 || true)
  fi
  
  if [ -n "$INTERNAL_DIR" ]; then
    cp -r "$INTERNAL_DIR"/* "$IDE_TARGET_DIR/"
  else
    echo "Error: Could not locate stm32cubeide directory inside extracted .deb payload."
    exit 1
  fi
  rm -rf "$WORKDIR"
else
  TAR_FILE=$(find "$TEMP_DIR" -name "*.tar.gz" -o -name "*.tar.bz2" | head -n 1 || true)
  if [ -n "$TAR_FILE" ]; then
    tar -xf "$TAR_FILE" -C "$IDE_TARGET_DIR" --strip-components=1
  else
    echo "Error: Neither .deb nor tarball payload found inside installer."
    exit 1
  fi
fi

# 5. Clean up temporary installer artifacts
rm -f "$INSTALLER"
rm -rf "$TEMP_DIR"
rm -f "$ARCHIVE_ZIP"

# 6. Install udev rules manually if present
UDEV_RULE=$(find "$IDE_TARGET_DIR" -name "*stlink*.rules" 2>/dev/null | head -n 1 || true)
if [ -n "$UDEV_RULE" ]; then
  echo "==> Installing ST-LINK udev rules..."
  sudo cp "$UDEV_RULE" /etc/udev/rules.d/ 2>/dev/null || true
  sudo udevadm control --reload-rules || true
  sudo udevadm trigger || true
fi

# 7. Create symlink in ~/.local/bin
mkdir -p "$HOME/.local/bin"
if [ -f "$IDE_TARGET_DIR/stm32cubeide" ]; then
  ln -sf "$IDE_TARGET_DIR/stm32cubeide" "$HOME/.local/bin/stm32cubeide"
else
  echo "Error: stm32cubeide executable not found in $IDE_TARGET_DIR."
  exit 1
fi

# 8. Extract official product icon directly from product branding bundle
echo "==> Setting up official launcher icon..."
mkdir -p "$HICOLOR_DIR" "$DESKTOP_DIR" "$TMP_IDE_ICON"

# Clean legacy desktop entries
rm -f "$DESKTOP_DIR"/st-com-stm32cubeide*.desktop \
      "$DESKTOP_DIR"/stm32cubeide*.desktop \
      "$DESKTOP_DIR"/STM32CubeIDE*.desktop \
      "$HOME/Desktop"/STM32CubeIDE*.desktop

ICON_DEST="$HICOLOR_DIR/stm32cubeide.png"

# Target the official product plugin JAR directly
PRODUCT_JAR=$(find "$IDE_TARGET_DIR/plugins" -type f -name "st.stm32cube.ide.mcu.product_*.jar" 2>/dev/null | head -n 1 || true)

if [ -n "$PRODUCT_JAR" ]; then
  # Extract product branding PNGs
  unzip -q -o "$PRODUCT_JAR" "st.stm32cube.ide.mcu.product.png" "icon.png" "icons/stm32cubeide.png" -d "$TMP_IDE_ICON" 2>/dev/null || true
fi

# Find extracted official image
EXTRACTED_ICON=$(find "$TMP_IDE_ICON" -type f \( -name "st.stm32cube.ide.mcu.product.png" -o -name "icon.png" -o -name "stm32cubeide.png" \) 2>/dev/null | head -n 1 || true)

if [ -n "$EXTRACTED_ICON" ]; then
  # Sanitize and convert image to clean RGBA via Pillow to guarantee Wayland/COSMIC compatibility
  python3 -c "
from PIL import Image
img = Image.open('$EXTRACTED_ICON').convert('RGBA')
img.resize((256, 256)).save('$ICON_DEST', 'PNG')
" 2>/dev/null || cp -f "$EXTRACTED_ICON" "$ICON_DEST"
  echo "✔ Official STM32CubeIDE branding icon extracted and installed."
else
  # Hard fallback: direct high-res official branding icon copy
  echo "==> Falling back to icon search across target dir..."
  FALLBACK_ICON=$(find "$IDE_TARGET_DIR" -type f -name "STM32CubeIDE_icon_256px.png" 2>/dev/null | head -n 1 || true)
  if [ -n "$FALLBACK_ICON" ]; then
    python3 -c "
from PIL import Image
img = Image.open('$FALLBACK_ICON').convert('RGBA')
img.save('$ICON_DEST', 'PNG')
" 2>/dev/null || cp -f "$FALLBACK_ICON" "$ICON_DEST"
  fi
fi

rm -rf "$TMP_IDE_ICON"
chmod 644 "$ICON_DEST" 2>/dev/null || true

# Generate launcher using base icon name
DESKTOP_FILE="$DESKTOP_DIR/stm32cubeide.desktop"

cat << EOF > "$DESKTOP_FILE"
[Desktop Entry]
Version=1.0
Type=Application
Name=STM32CubeIDE
Comment=STMicroelectronics Integrated Development Environment for STM32
Exec=$IDE_TARGET_DIR/stm32cubeide %F
Path=$IDE_TARGET_DIR
Icon=stm32cubeide
Terminal=false
Categories=Development;IDE;
StartupWMClass=Eclipse
EOF

chmod 755 "$DESKTOP_FILE"

# 9. Refresh databases & restart COSMIC services
echo "==> Refreshing databases and clearing COSMIC launcher cache..."
gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
if command -v update-desktop-database &>/dev/null; then
  update-desktop-database "$DESKTOP_DIR" || true
fi

# Purge COSMIC local app grid cache & restart shell components
rm -rf "$HOME/.cache/cosmic" "$HOME/.cache/pop-launcher" 2>/dev/null || true
killall -9 cosmic-app-library pop-launcher cosmic-panel cosmic-applet-applications 2>/dev/null || true

echo "==> Success! STM32CubeIDE installed directly to $IDE_TARGET_DIR"
echo "==> Symlinked to $HOME/.local/bin/stm32cubeide"
echo "==> Launcher created at $DESKTOP_FILE"
