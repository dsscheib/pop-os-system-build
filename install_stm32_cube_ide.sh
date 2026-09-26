#!/usr/bin/env bash
set -euo pipefail

# Target installation directory
IDE_TARGET_DIR="$HOME/STMicroelectronics/STM32Cube/stm32cubeide"
TEMP_DIR="/tmp/stm32cubeide_extract"
DESKTOP_DIR="$HOME/.local/share/applications"

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
  # Unpack .deb archive directly using ar and tar (bypasses dpkg version syntax check)
  WORKDIR="/tmp/deb_unpack"
  rm -rf "$WORKDIR" && mkdir -p "$WORKDIR"
  
  ar x "$DEB_FILE" --output="$WORKDIR"
  
  TAR_DATA=$(find "$WORKDIR" -name "data.tar.*" | head -n 1)
  tar -xf "$TAR_DATA" -C "$WORKDIR"
  
  # Locate internal stm32cubeide installation folder within extracted deb
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
  # Fallback if payload contains raw tarballs instead of a .deb file
  TAR_FILE=$(find "$TEMP_DIR" -name "*.tar.gz" -o -name "*.tar.bz2" | head -n 1 || true)
  if [ -n "$TAR_FILE" ]; then
    tar -xf "$TAR_FILE" -C "$IDE_TARGET_DIR" --strip-components=1
  else
    echo "Error: Neither .deb nor tarball payload found inside installer."
    exit 1
  fi
fi

# 5. Clean up temporary directories
rm -f "$INSTALLER"
rm -rf "$TEMP_DIR"
rm -f "$ARCHIVE_ZIP"

# 6. Install udev rules manually if present
UDEV_RULE=$(find "$IDE_TARGET_DIR" -name "*stlink*.rules" 2>/dev/null | head -n 1 || true)
if [ -n "$UDEV_RULE" ]; then
  echo "==> Installing ST-LINK udev rules..."
  sudo cp "$UDEV_RULE" /etc/udev/rules.d/
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

# 8. Create desktop launcher (.desktop)
echo "==> Creating .desktop launcher..."
mkdir -p "$DESKTOP_DIR"

ICON_PATH=$(find "$IDE_TARGET_DIR" -name "icon.xpm" -o -name "st_logo.png" -o -name "icon.png" 2>/dev/null | head -n 1 || true)

cat << EOF > "$DESKTOP_DIR/st-com-stm32cubeide.desktop"
[Desktop Entry]
Type=Application
Name=STM32CubeIDE
Comment=STMicroelectronics Integrated Development Environment for STM32
Exec=$IDE_TARGET_DIR/stm32cubeide %F
Icon=${ICON_PATH:-$IDE_TARGET_DIR/icon.xpm}
Terminal=false
Categories=Development;IDE;
StartupWMClass=stm32cubeide
EOF

chmod +x "$DESKTOP_DIR/st-com-stm32cubeide.desktop"

# Update desktop application database if utility is available
if command -v update-desktop-database &>/dev/null; then
  update-desktop-database "$DESKTOP_DIR" || true
fi

echo "==> Success! STM32CubeIDE installed directly to $IDE_TARGET_DIR"
echo "==> Symlinked to $HOME/.local/bin/stm32cubeide"
echo "==> Launcher created at $DESKTOP_DIR/st-com-stm32cubeide.desktop"
