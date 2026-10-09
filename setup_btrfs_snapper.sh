#!/usr/bin/env bash

# ==============================================================================
# BTRFS SNAPPER & AUTOMATED APT SNAPSHOT SETUP
# Targets: Pop!_OS / Ubuntu / Debian with a Btrfs root filesystem
# ==============================================================================

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

if [[ $EUID -ne 0 ]]; then
  SUDO="sudo"
else
  SUDO=""
fi

# ------------------------------------------------------------------------------
# 1. INSTALL DEPENDENCIES & SNAPPER-ROLLBACK
# ------------------------------------------------------------------------------
install_packages() {
  log_info "Installing Btrfs tools, Snapper, and core packages..."
  $SUDO apt update
  $SUDO apt install -y \
    btrfs-progs \
    python3-btrfsutil \
    snapper \
    snapper-gui \
    libpam-snapper \
    python3 \
    git

  log_info "Installing pinned snapper-rollback to /usr/local/bin..."
  
  local TEMP_DIR
  TEMP_DIR=$(create_secure_tmpdir "snapper_rollback")
  
  # Pin to immutable verified commit hash (supply-chain hardening)
  local ROLLBACK_COMMIT="04488f2350e8ec214277fe7de608492a9ee665a7"
  if git clone https://github.com/jrabinow/snapper-rollback.git "$TEMP_DIR/snapper-rollback"; then
    (
      cd "$TEMP_DIR/snapper-rollback"
      git checkout -q "$ROLLBACK_COMMIT"
    )
    $SUDO install -o root -g root -m 0755 "$TEMP_DIR/snapper-rollback/snapper-rollback.py" /usr/local/bin/snapper-rollback
    log_info "Installed snapper-rollback pinned at $ROLLBACK_COMMIT."
  else
    log_warn "Failed to clone snapper-rollback repository."
  fi
  rm -rf "$TEMP_DIR"

  # Detect primary Btrfs block device automatically
  local RAW_DEV
  RAW_DEV=$(findmnt -n -o SOURCE / | cut -d'[' -f1)

  # If config exists, create a timestamped backup before updating
  if [ -f /etc/snapper-rollback.conf ]; then
    log_info "Existing config found. Creating backup at /etc/snapper-rollback.conf.bak..."
    $SUDO cp /etc/snapper-rollback.conf "/etc/snapper-rollback.conf.bak_$(date +%Y%m%d_%H%M%S)"
  fi

  # Write updated configuration
  $SUDO tee /etc/snapper-rollback.conf > /dev/null <<EOF
[root]
dev = ${RAW_DEV}
subvol_main = @
subvol_snapshots = @snapshots
mountpoint = /btrfsroot
EOF

  # Verify section header matches what snapper-rollback expects
  if ! grep -q "^\[root\]" /etc/snapper-rollback.conf; then
    log_warn "/etc/snapper-rollback.conf is missing the [root] section header!"
  fi
}

# ------------------------------------------------------------------------------
# 2. SUBVOLUME VERIFICATION (Protects against corrupted live root migrations)
# ------------------------------------------------------------------------------
verify_subvolumes() {
  log_info "Checking current Btrfs subvolume layout..."

  local ROOT_SUBVOL
  ROOT_SUBVOL=$(findmnt -n -o OPTIONS / | grep -o 'subvol=[^,]*' || true)

  if [[ "$ROOT_SUBVOL" =~ subvol=/?@($|/) ]]; then
    log_info "Subvolume '/@' is verified and active."
    return 0
  fi

  if $SUDO btrfs subvolume list / 2>/dev/null | grep -q 'path @$'; then
    log_warn "Subvolume '/@' exists on disk but root is currently mounted without it."
    log_warn "Please ensure your kernelstub boot options include 'rootflags=subvol=@' and reboot."
    return 0
  fi

  log_error "Flat Btrfs structure detected on active root filesystem."
  log_error "To prevent data corruption, subvolumes must be created from the Pop!_OS Live USB installer."
  log_error "Please run: sudo ./setup_btrfs_live_install.sh /dev/sdXN before continuing."
  exit 1
}

# ------------------------------------------------------------------------------
# 3. CONFIGURE SNAPPER FOR ROOT (/@)
# ------------------------------------------------------------------------------
setup_snapper_root() {
  log_info "Configuring Snapper for pre-mounted @snapshots layout..."

  # 1. Ensure config template directory exists
  $SUDO mkdir -p /etc/snapper/configs

  # 2. Copy the template to create /etc/snapper/configs/root if missing
  if [ ! -f /etc/snapper/configs/root ]; then
    $SUDO cp /usr/share/snapper/config-templates/default /etc/snapper/configs/root
  fi

  # 3. Register 'root' config inside /etc/default/snapper
  $SUDO touch /etc/default/snapper
  if grep -q 'SNAPPER_CONFIGS=' /etc/default/snapper; then
    $SUDO sed -i 's/SNAPPER_CONFIGS=.*/SNAPPER_CONFIGS="root"/' /etc/default/snapper
  else
    echo 'SNAPPER_CONFIGS="root"' | $SUDO tee -a /etc/default/snapper >/dev/null
  fi

  # 4. Set mount point, filesystem type, group permissions, and retention limits
  $SUDO sed -i 's|^SUBVOLUME=.*|SUBVOLUME="/"|' /etc/snapper/configs/root
  $SUDO sed -i 's|^FSTYPE=.*|FSTYPE="btrfs"|' /etc/snapper/configs/root
  $SUDO sed -i 's|^ALLOW_GROUPS=.*|ALLOW_GROUPS="sudo"|' /etc/snapper/configs/root
  $SUDO sed -i 's|^NUMBER_MIN_AGE=.*|NUMBER_MIN_AGE="1800"|' /etc/snapper/configs/root
  $SUDO sed -i 's|^NUMBER_LIMIT=.*|NUMBER_LIMIT="10"|' /etc/snapper/configs/root
  $SUDO sed -i 's|^NUMBER_LIMIT_IMPORTANT=.*|NUMBER_LIMIT_IMPORTANT="5"|' /etc/snapper/configs/root
  $SUDO sed -i 's|^TIMELINE_CREATE=.*|TIMELINE_CREATE="no"|' /etc/snapper/configs/root
  $SUDO sed -i 's|^TIMELINE_CLEANUP=.*|TIMELINE_CLEANUP="yes"|' /etc/snapper/configs/root

  # 5. Set permissions on /.snapshots allowing sudo group access for snapper-gui
  $SUDO chown root:sudo /.snapshots 2>/dev/null || $SUDO chown root:root /.snapshots
  $SUDO chmod 750 /.snapshots
}

# ------------------------------------------------------------------------------
# 4. HARDENED AUTOMATIC PRE/POST APT SNAPSHOTS (Prevents /var/tmp TOCTOU & DoS)
# ------------------------------------------------------------------------------
setup_apt_snapshots() {
  log_info "Configuring hardened paired APT hook for Snapper..."

  # Create secure state directory on tmpfs (/run)
  $SUDO mkdir -p /run/snapper
  $SUDO chmod 0755 /run/snapper

  cat <<'HOOK' | $SUDO tee /etc/apt/apt.conf.d/80snapper > /dev/null
// Hardened Automatic Snapper snapshots before and after APT operations
DPkg::Pre-Invoke {
  "if [ -x /usr/bin/snapper ] && [ -e /etc/snapper/configs/root ]; then \
    mkdir -p /run/snapper && chmod 0755 /run/snapper; \
    rm -f /run/snapper/apt-pre.num 2>/dev/null || true; \
    PRE_NUM=$(snapper -c root create -t pre -c number -p -d 'APT Pre-Update Snapshot' 2>/dev/null || true); \
    if [ -n \"$PRE_NUM\" ]; then \
      echo \"$PRE_NUM\" > /run/snapper/apt-pre.num; \
      chmod 0600 /run/snapper/apt-pre.num; \
    fi; \
    snapper -c root cleanup number 2>/dev/null || true; \
  fi";
};

DPkg::Post-Invoke {
  "if [ -x /usr/bin/snapper ] && [ -f /run/snapper/apt-pre.num ]; then \
    PRE_NUM=$(cat /run/snapper/apt-pre.num 2>/dev/null || true); \
    if echo \"$PRE_NUM\" | grep -qE '^[0-9]+$'; then \
      snapper -c root create -t post -c number --pre-number=\"$PRE_NUM\" -d 'APT Post-Update Snapshot' 2>/dev/null || true; \
    fi; \
    rm -f /run/snapper/apt-pre.num 2>/dev/null || true; \
    snapper -c root cleanup number 2>/dev/null || true; \
  fi";
};
HOOK
}

# ------------------------------------------------------------------------------
# 5. ENABLE SNAPPER CLEANUP & BOOT TIMERS
# ------------------------------------------------------------------------------
enable_services() {
  log_info "Enabling Snapper automatic maintenance timers..."
  $SUDO systemctl daemon-reload
  $SUDO systemctl enable --now snapper-cleanup.timer
  $SUDO systemctl enable --now snapper-boot.timer 2>/dev/null || true
}

# ------------------------------------------------------------------------------
# MAIN EXECUTION
# ------------------------------------------------------------------------------
main() {
  install_packages

  local ROOT_FSTYPE
  ROOT_FSTYPE=$(findmnt -n -o FSTYPE /)
  if [[ "$ROOT_FSTYPE" != "btrfs" ]]; then
    log_error "Root filesystem '/' is currently '$ROOT_FSTYPE', not Btrfs."
    log_error "Snapper automated snapshots require a Btrfs root filesystem."
    exit 1
  fi

  verify_subvolumes
  setup_snapper_root
  setup_apt_snapshots
  enable_services

  log_info "Btrfs and Snapper configuration complete!"
}

main "$@"
