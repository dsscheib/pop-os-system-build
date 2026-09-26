#!/usr/bin/env bash
set -euo pipefail

# Target installation directory
IDE_TARGET_DIR="$HOME/STMicroelectronics/STM32Cube/stm32cubeide"
TEMP_EXTRACT_DIR="/tmp/stm32cubeide_installer"

# 1. Locate the downloaded zip archive
ARCHIVE_ZIP=$(ls stm32cubeide_*.sh.zip stm32cubeide_*.zip 2>/dev/null | head -n 1 || true)

if [ -z "$ARCHIVE_ZIP" ]; then
  echo "Error: No 'stm32cubeide_*.zip' file found in current directory."
  exit 1
fi

echo "==> Extracting $ARCHIVE_ZIP..."
unzip -q -o "$ARCHIVE_ZIP"

# 2. Identify extracted shell installer script
INSTALLER=$(ls stm32cubeide_*.sh 2>/dev/null | head -n 1 || true)

if [ -z "$INSTALLER" ]; then
  echo "Error: STM32CubeIDE installer script not found after extraction."
  exit 1
fi

chmod +x "$INSTALLER"

# 3. Ensure required system dependencies are installed
echo "==> Installing system dependencies..."
if command -v apt-get &>/dev/null; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq libusb-1.0-0 libftdi1-2 systemd
fi

# 4. Extract payload using Makeself flags
echo "==> Unpacking installer payload to temporary directory..."
rm -rf "$TEMP_EXTRACT_DIR"
./"$INSTALLER" --noexec --target "$TEMP_EXTRACT_DIR"

# 5. Run the inner installer script passing arguments AFTER '--'
echo "==> Installing STM32CubeIDE silently..."
if [ -f "$TEMP_EXTRACT_DIR/setup.sh" ]; then
  # Execute inner script directly
  sudo "$TEMP_EXTRACT_DIR/setup.sh" --quiet --prefix "$IDE_TARGET_DIR" || \
  sudo "$TEMP_EXTRACT_DIR/setup.sh" -y "$IDE_TARGET_DIR"
else
  # Fallback to makeself argument separator '--'
  sudo ./"$INSTALLER" --quiet -- --eula-accept --prefix "$IDE_TARGET_DIR"
fi

# 6. Clean up temporary files
rm -f "$INSTALLER"
rm -rf "$TEMP_EXTRACT_DIR"

# 7. Symlink to local bin
mkdir -p "$HOME/.local/bin"

if [ -f "$IDE_TARGET_DIR/stm32cubeide" ]; then
  INSTALL_DIR="$IDE_TARGET_DIR"
else
  INSTALL_DIR=$(ls -d "$IDE_TARGET_DIR"/stm32cubeide_* 2>/dev/null | tail -n 1 || true)
fi

if [ -z "$INSTALL_DIR" ] || [ ! -f "$INSTALL_DIR/stm32cubeide" ]; then
  # Check system fallback path if custom prefix was overridden by the internal installer
  INSTALL_DIR=$(ls -d /opt/st/stm32cubeide_* 2>/dev/null | tail -n 1 || true)
fi

if [ -n "$INSTALL_DIR" ] && [ -f "$INSTALL_DIR/stm32cubeide" ]; then
  echo "==> Symlinking STM32CubeIDE to $HOME/.local/bin/stm32cubeide..."
  ln -sf "$INSTALL_DIR/stm32cubeide" "$HOME/.local/bin/stm32cubeide"

  # Reload udev rules
  if [ -d /etc/udev/rules.d ]; then
    echo "==> Reloading udev rules..."
    sudo udevadm control --reload-rules || true
    sudo udevadm trigger || true
  fi

  echo "==> Installation complete! Run 'stm32cubeide' from your terminal."
else
  echo "Error: Installation failed. Could not locate installed stm32cubeide executable."
  exit 1
fi
