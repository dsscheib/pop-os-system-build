#!/usr/bin/env bash
# ==============================================================================
# INSTALL STM32CubeIDE
# Targets: Pop!_OS / Ubuntu x86_64
# ==============================================================================

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

IDE_TARGET_DIR="$HOME/STMicroelectronics/STM32Cube/stm32cubeide"
DESKTOP_DIR="$HOME/.local/share/applications"
HICOLOR_DIR="$HOME/.local/share/icons/hicolor/256x256/apps"

# 1. Locate the downloaded zip archive (searches $1, current dir, and ~/Downloads)
EXPLICIT_ARG="${1:-}"
ARCHIVE_ZIP=$(find_archive "$EXPLICIT_ARG" "stm32cubeide_*.sh.zip" "stm32cubeide_*.zip" || true)

if [[ -z "$ARCHIVE_ZIP" || ! -f "$ARCHIVE_ZIP" ]]; then
  log_error "No 'stm32cubeide_*.zip' found in current directory or ~/Downloads."
  log_error "Usage: $0 [path/to/stm32cubeide_*.zip]"
  exit 1
fi

# Set up isolated secure temporary directory
BUILD_TMP=$(create_secure_tmpdir "stm32cubeide")
trap 'rm -rf "$BUILD_TMP"' EXIT

log_info "Unzipping $ARCHIVE_ZIP into secure temporary directory..."
unzip -q -o "$ARCHIVE_ZIP" -d "$BUILD_TMP"

# 2. Locate shell installer script inside temporary directory
INSTALLER=$(find "$BUILD_TMP" -maxdepth 2 -name "stm32cubeide_*.sh" 2>/dev/null | head -n 1 || true)

if [[ -z "$INSTALLER" || ! -f "$INSTALLER" ]]; then
  log_error "STM32CubeIDE shell installer (.sh) not found after extraction."
  exit 1
fi

chmod +x "$INSTALLER"

# 3. Unpack installer payload to temp folder
EXTRACT_DIR="$BUILD_TMP/payload_extract"
mkdir -p "$EXTRACT_DIR"
log_info "Unpacking makeself payload..."
"$INSTALLER" --noexec --target "$EXTRACT_DIR"

# 4. Extract binaries directly without dpkg / apt validation
log_info "Preparing target directory $IDE_TARGET_DIR..."
rm -rf "$IDE_TARGET_DIR"
mkdir -p "$IDE_TARGET_DIR"

DEB_FILE=$(find "$EXTRACT_DIR" -name "*.deb" 2>/dev/null | head -n 1 || true)

if [[ -n "$DEB_FILE" ]]; then
  WORKDIR="$BUILD_TMP/deb_unpack"
  mkdir -p "$WORKDIR"
  
  ar x "$DEB_FILE" --output="$WORKDIR"
  
  TAR_DATA=$(find "$WORKDIR" -name "data.tar.*" | head -n 1)
  tar -xf "$TAR_DATA" -C "$WORKDIR"
  
  INTERNAL_DIR=$(find "$WORKDIR" -type d -name "stm32cubeide_*" | head -n 1 || true)
  if [[ -z "$INTERNAL_DIR" ]]; then
    INTERNAL_DIR=$(find "$WORKDIR" -type f -name "stm32cubeide" -exec dirname {} \; | head -n 1 || true)
  fi
  
  if [[ -n "$INTERNAL_DIR" ]]; then
    cp -r "$INTERNAL_DIR"/* "$IDE_TARGET_DIR/"
  else
    log_error "Could not locate stm32cubeide directory inside extracted .deb payload."
    exit 1
  fi
else
  TAR_FILE=$(find "$EXTRACT_DIR" -name "*.tar.gz" -o -name "*.tar.bz2" 2>/dev/null | head -n 1 || true)
  if [[ -n "$TAR_FILE" ]]; then
    tar -xf "$TAR_FILE" -C "$IDE_TARGET_DIR" --strip-components=1
  else
    log_error "Neither .deb nor tarball payload found inside installer."
    exit 1
  fi
fi

# 5. Install udev rules securely if present
install_stlink_rules "$IDE_TARGET_DIR"

# 6. Create symlink in ~/.local/bin
mkdir -p "$HOME/.local/bin"
if [[ -f "$IDE_TARGET_DIR/stm32cubeide" ]]; then
  ln -sf "$IDE_TARGET_DIR/stm32cubeide" "$HOME/.local/bin/stm32cubeide"
else
  log_error "stm32cubeide executable not found in $IDE_TARGET_DIR."
  exit 1
fi

# 7. Setup launcher icon using native icon.xpm
log_info "Setting up launcher icon..."
mkdir -p "$HICOLOR_DIR" "$DESKTOP_DIR"
ICON_DEST="$HICOLOR_DIR/stm32cubeide.png"
NATIVE_XPM="$IDE_TARGET_DIR/icon.xpm"

# Purge legacy launcher entries
rm -f "$DESKTOP_DIR"/st-com-stm32cubeide*.desktop \
      "$DESKTOP_DIR"/stm32cubeide*.desktop \
      "$DESKTOP_DIR"/STM32CubeIDE*.desktop \
      "$HOME/Desktop"/STM32CubeIDE*.desktop 2>/dev/null || true

if [[ -f "$NATIVE_XPM" ]]; then
  convert_icon_to_png "$NATIVE_XPM" "$ICON_DEST"
else
  log_warn "Native icon.xpm not found at $NATIVE_XPM."
fi

# Generate Freedesktop .desktop launcher
DESKTOP_FILE="$DESKTOP_DIR/stm32cubeide.desktop"

cat << EOF > "$DESKTOP_FILE"
[Desktop Entry]
Version=1.0
Type=Application
Name=STM32CubeIDE
Comment=STMicroelectronics Integrated Development Environment for STM32
Exec=$IDE_TARGET_DIR/stm32cubeide %F
Path=$IDE_TARGET_DIR
Icon=$ICON_DEST
Terminal=false
Categories=Development;IDE;
StartupWMClass=stm32cubeide
EOF

chmod 755 "$DESKTOP_FILE"

# 8. Refresh icon cache & COSMIC desktop state
refresh_desktop_environment "$DESKTOP_DIR"

log_info "Success! STM32CubeIDE installed to $IDE_TARGET_DIR"
log_info "Symlinked to $HOME/.local/bin/stm32cubeide"
log_info "Launcher created at $DESKTOP_FILE"
