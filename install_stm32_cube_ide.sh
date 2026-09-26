#!/usr/bin/env bash
set -euo pipefail

# Define target installation directory
IDE_TARGET_DIR="$HOME/STMicroelectronics/STM32Cube/stm32cubeide"

# 1. Locate the downloaded zip archive
ARCHIVE_ZIP=$(ls stm32cubeide_*.sh.zip 2>/dev/null | head -n 1 || true)

if [ -z "$ARCHIVE_ZIP" ]; then
  echo "Error: No 'stm32cubeide_*.sh.zip' file found in current directory."
  exit 1
fi

echo "==> Extracting $ARCHIVE_ZIP..."
unzip -q -o "$ARCHIVE_ZIP"

# 2. Identify the Linux shell installer script
INSTALLER=$(ls stm32cubeide_*.sh 2>/dev/null | head -n 1 || true)

if [ -z "$INSTALLER" ]; then
  echo "Error: STM32CubeIDE shell installer (.sh) not found after extraction."
  exit 1
fi

chmod +x "$INSTALLER"

# 3. Ensure required system dependencies are installed
echo "==> Installing system dependencies..."
if command -v apt-get &>/dev/null; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq libusb-1.0-0 libftdi1-2 systemd
elif command -v dnf &>/dev/null; then
  sudo dnf install -y -q libusb1 libftdi systemd
fi

# 4. Execute the silent installer with elevated privileges
# --eula-accept bypasses the interactive license agreement prompt
# --quiet suppresses the installation wizard GUI
echo "==> Installing STM32CubeIDE silently..."
yes | sudo ./"$INSTALLER" --quiet --target "$IDE_TARGET_DIR"

# 5. Clean up temporary installer artifacts
rm -f "$INSTALLER"

# 6. Locate installed directory and create a global symlink
# STM32CubeIDE installs into /opt/st/stm32cubeide_<version>/ by default
INSTALL_DIR=$(ls -d /opt/st/stm32cubeide_* 2>/dev/null | tail -n 1 || true)

if [ -n "$INSTALL_DIR" ] && [ -f "$INSTALL_DIR/stm32cubeide" ]; then
  echo "==> Symlinking STM32CubeIDE to /usr/local/bin..."
  sudo ln -sf "$INSTALL_DIR/stm32cubeide" /usr/local/bin/stm32cubeide

  # Reload udev rules installed by ST-LINK drivers
  if [ -d /etc/udev/rules.d ]; then
    echo "==> Reloading udev rules..."
    sudo udevadm control --reload-rules || true
    sudo udevadm trigger || true
  fi

  echo "==> Installation complete! Run 'stm32cubeide' from any terminal."
else
  echo "Warning: Expected installation directory in /opt/st/ not found."
fi
