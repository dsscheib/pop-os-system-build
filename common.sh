#!/usr/bin/env bash
# ==============================================================================
# COMMON UTILITIES AND HELPERS
# Shared across system build, STMicroelectronics, and post-install scripts
# ==============================================================================

# Ensure standard error handling when sourced
set -euo pipefail

# ------------------------------------------------------------------------------
# LOGGING HELPERS
# ------------------------------------------------------------------------------
log_info()  { echo -e "\033[0;34m[INFO]\033[0m $1"; }
log_warn()  { echo -e "\033[0;33m[WARN]\033[0m $1"; }
log_error() { echo -e "\033[0;31m[ERROR]\033[0m $1"; }

# ------------------------------------------------------------------------------
# SECURE TEMPORARY DIRECTORY CREATION
# Usage: TMP_DIR=$(create_secure_tmpdir "prefix")
# Creates an unguessable directory in /tmp with mode 0700
# ------------------------------------------------------------------------------
create_secure_tmpdir() {
  local prefix="${1:-build}"
  local tmp_dir
  tmp_dir=$(mktemp -d -t "${prefix}.XXXXXX")
  chmod 0700 "$tmp_dir"
  echo "$tmp_dir"
}

# ------------------------------------------------------------------------------
# LOCATE DOWNLOAD ARCHIVE / INSTALLER
# Usage: find_archive "optional_explicit_path" "pattern1" ["pattern2"...]
# Checks:
#   1. Explicit user-provided file argument (if passed and valid)
#   2. Current working directory ($PWD)
#   3. User Downloads directory ($HOME/Downloads)
# ------------------------------------------------------------------------------
find_archive() {
  local explicit_path="$1"
  shift

  # If an explicit path was supplied and exists, return it
  if [[ -n "$explicit_path" && -f "$explicit_path" ]]; then
    echo "$explicit_path"
    return 0
  fi

  local patterns=("$@")
  local search_dirs=("$PWD" "$HOME/Downloads")

  for dir in "${search_dirs[@]}"; do
    if [[ -d "$dir" ]]; then
      for pattern in "${patterns[@]}"; do
        local match
        match=$(find "$dir" -maxdepth 1 -name "$pattern" 2>/dev/null | head -n 1 || true)
        if [[ -n "$match" && -f "$match" ]]; then
          echo "$match"
          return 0
        fi
      done
    fi
  done

  return 1
}

# ------------------------------------------------------------------------------
# CONVERT NATIVE ICON (XPM / ICO / PNG) TO 256x256 PNG
# Usage: convert_icon_to_png "$source_icon" "$dest_png_path"
# ------------------------------------------------------------------------------
convert_icon_to_png() {
  local src_icon="$1"
  local dest_png="$2"

  if [[ ! -f "$src_icon" ]]; then
    log_error "Source icon not found: $src_icon"
    return 1
  fi

  mkdir -p "$(dirname "$dest_png")"

  # 1. Attempt high-quality conversion using Python Pillow (PIL)
  if python3 -c "
from PIL import Image
import sys
try:
    img = Image.open(sys.argv[1]).convert('RGBA')
    img = img.resize((256, 256), Image.Resampling.LANCZOS)
    img.save(sys.argv[2], 'PNG')
    sys.exit(0)
except Exception:
    sys.exit(1)
" "$src_icon" "$dest_png" 2>/dev/null; then
    chmod 644 "$dest_png"
    log_info "Converted icon using Python PIL: $src_icon -> $dest_png"
    return 0
  fi

  # 2. Fallback to ImageMagick convert
  if command -v convert &>/dev/null; then
    if convert "$src_icon" -resize 256x256 "$dest_png" 2>/dev/null; then
      chmod 644 "$dest_png"
      log_info "Converted icon using ImageMagick: $src_icon -> $dest_png"
      return 0
    fi
  fi

  # 3. Fallback: If source is already a PNG, copy directly
  if [[ "$src_icon" == *.png ]]; then
    cp -f "$src_icon" "$dest_png"
    chmod 644 "$dest_png"
    log_info "Copied native PNG icon: $src_icon -> $dest_png"
    return 0
  fi

  log_warn "Could not convert $src_icon to PNG. Please install python3-pil or imagemagick."
  return 1
}

# ------------------------------------------------------------------------------
# HARDENED INSTALL ST-LINK UDEV RULES
# Usage: install_stlink_rules "$search_directory"
# Validates rules to prevent arbitrary command execution via RUN/PROGRAM
# ------------------------------------------------------------------------------
install_stlink_rules() {
  local search_dir="$1"
  local udev_rule

  udev_rule=$(find "$search_dir" -name "*stlink*.rules" 2>/dev/null | head -n 1 || true)
  if [[ -n "$udev_rule" && -f "$udev_rule" ]]; then
    # Security verification: refuse rules containing command execution directives
    if grep -qE '\b(RUN|PROGRAM)\b' "$udev_rule"; then
      log_error "Security Alert: Refusing to install udev rule containing dangerous RUN/PROGRAM directives: $udev_rule"
      return 1
    fi

    log_info "Installing verified ST-LINK udev rules from $udev_rule..."
    sudo install -o root -g root -m 0644 "$udev_rule" /etc/udev/rules.d/
    sudo udevadm control --reload-rules 2>/dev/null || true
    sudo udevadm trigger 2>/dev/null || true
  fi
}

# ------------------------------------------------------------------------------
# REFRESH DESKTOP ENVIRONMENT & COSMIC LAUNCHER
# Usage: refresh_desktop_environment "$desktop_dir"
# ------------------------------------------------------------------------------
refresh_desktop_environment() {
  local desktop_dir="${1:-$HOME/.local/share/applications}"

  log_info "Refreshing desktop icon cache and application databases..."
  gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
  
  if command -v update-desktop-database &>/dev/null; then
    update-desktop-database "$desktop_dir" 2>/dev/null || true
  fi

  # Purge COSMIC application grid cache and gracefully reload cosmic-app-library
  rm -rf "$HOME/.cache/cosmic" "$HOME/.cache/pop-launcher" 2>/dev/null || true
  killall cosmic-app-library 2>/dev/null || true
}
