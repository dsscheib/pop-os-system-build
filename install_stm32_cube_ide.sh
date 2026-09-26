#!/usr/bin/env bash
set -euo pipefail

# Target installation directory
IDE_TARGET_DIR="$HOME/STMicroelectronics/STM32Cube/stm32cubeide"

# 1. Locate the downloaded zip archive
ARCHIVE_ZIP=$(ls stm32cubeide_*.sh.zip stm32cubeide_*.zip 2>/dev/null | head -n 1 || true)

if [ -z "$ARCHIVE_ZIP" ]; then
  echo "Error: No 'stm32cubeide_*.zip' file found in current directory."
  exit 1
fi

echo "==> Extracting $ARCHIVE_ZIP..."
unzip -q -o "$ARCHIVE_ZIP"

# 2. Identify extracted shell installer script or installer executable
INSTALLER=$(ls stm32cubeide_*.sh 2>/dev/null | head -n 1 || true)

if [ -z "$INSTALLER" ]; then
  echo "Error: STM32CubeIDE installer script not found after extraction."
  exit 1
fi

chmod +x "$INSTALLER"

# 3. Ensure required dependencies are installed
echo "==> Installing system dependencies..."
if command -v apt-get &>/dev/null; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq libusb-1.0-0 libftdi1-2 systemd
fi

# 4. Execute the installer
# Using --prefix or standard installer flags instead of --target
echo "==> Installing STM32CubeIDE silently..."
sudo ./"$INSTALLER" --eula-accept --quiet --prefix "$IDE_TARGET_DIR"

# 5. Clean up installer scripts
rm -f "$INSTALLER"

# 6. Resolve actual installation directory and create local user symlink
mkdir -p "$HOME/.local/bin"

if [ -f "$IDE_TARGET_DIR/stm32cubeide" ]; then
  INSTALL_DIR="$IDE_TARGET_DIR"
else
  INSTALL_DIR=$(ls -d "$IDE_TARGET_DIR"/stm32cubeide_* 2>/dev/null | tail -n 1 || true)
fi

if [ -z "$INSTALL_DIR" ] || [ ! -f "$INSTALL_DIR/stm32cubeide" ]; then
  # Fallback check if it installed to /opt/st/
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
  echo "Error: Installation failed. Target directory $IDE_TARGET_DIR is empty."
  exit 1
fi#!/usr/bin/env bash
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
yes | ./"$INSTALLER" --quiet --target "$IDE_TARGET_DIR"

# 5. Clean up temporary installer artifacts
rm -f "$INSTALLER"

# 6. Locate installed directory within IDE_TARGET_DIR and create a global symlink
if [ -f "$IDE_TARGET_DIR/stm32cubeide" ]; then
  INSTALL_DIR="$IDE_TARGET_DIR"
else
  INSTALL_DIR=$(ls -d "$IDE_TARGET_DIR"/stm32cubeide_* 2>/dev/null | tail -n 1 || true)
fi

if [ -n "$INSTALL_DIR" ] && [ -f "$INSTALL_DIR/stm32cubeide" ]; then
  echo "==> Symlinking STM32CubeIDE to $HOME/.local/bin..."
  mkdir -p "$HOME/.local/bin"
  ln -sf "$INSTALL_DIR/stm32cubeide" "$HOME/.local/bin/stm32cubeide"

  # Reload udev rules installed by ST-LINK drivers
  if [ -d /etc/udev/rules.d ]; then
    echo "==> Reloading udev rules..."
    sudo udevadm control --reload-rules || true
    sudo udevadm trigger || true
  fi

  echo "==> Installation complete! Run 'stm32cubeide' from any terminal."
else
  echo "Warning: Expected installation directory in $IDE_TARGET_DIR not found."
fi
