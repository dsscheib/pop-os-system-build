#!/usr/bin/env bash

# ==============================================================================
# BTRFS SNAPPER & AUTOMATED APT SNAPSHOT SETUP
# Targets: Pop!_OS / Ubuntu / Debian with a Btrfs root filesystem
# ==============================================================================

set -euo pipefail

log_info()  { echo -e "\033[0;34m[INFO]\033[0m $1"; }
log_warn()  { echo -e "\033[0;33m[WARN]\033[0m $1"; }
log_error() { echo -e "\033[0;31m[ERROR]\033[0m $1"; }

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

  log_info "Installing snapper-rollback directly to /usr/local/bin..."
  
  TEMP_DIR=$(mktemp -d)
  git clone https://github.com/jrabinow/snapper-rollback.git "$TEMP_DIR/snapper-rollback"
  
  $SUDO cp "$TEMP_DIR/snapper-rollback/snapper-rollback.py" /usr/local/bin/snapper-rollback
  $SUDO chmod +x /usr/local/bin/snapper-rollback

  # Detect primary Btrfs block device automatically
  RAW_DEV=$(findmnt -n -o SOURCE / | cut -d'[' -f1)

  # If config exists, create a timestamped backup before updating
  if [ -f /etc/snapper-rollback.conf ]; then
      echo "Existing config found. Creating backup at /etc/snapper-rollback.conf.bak..."
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
      echo "WARNING: /etc/snapper-rollback.conf is missing the [root] section header!"
  fi

  rm -rf "$TEMP_DIR"
}

# ------------------------------------------------------------------------------
# 2. SUBVOLUME VERIFICATION / MIGRATION FALLBACK
# ------------------------------------------------------------------------------
migrate_subvolumes() {
  log_info "Checking current Btrfs subvolume layout..."

  ROOT_SUBVOL=$(findmnt -n -o OPTIONS / | grep -o 'subvol=[^,]*' || true)

  if [[ "$ROOT_SUBVOL" == "subvol=/@" ]]; then
    log_info "Subvolume '/@' is active."
    return 0
  fi

  if sudo btrfs subvolume list / | grep -q 'path @$'; then
    log_info "Subvolume '/@' exists. Ensuring active mount..."
    return 0
  fi

  log_warn "Flat Btrfs structure detected. Beginning non-destructive subvolume migration..."

  ROOT_DEV=$(findmnt -n -o SOURCE /)
  MNT_DIR=$(mktemp -d /tmp/btrfs-migrate.XXXXXX)

  log_info "Mounting top-level Btrfs volume (subvolid=5) at ${MNT_DIR}..."
  $SUDO mount -o subvolid=5 "$ROOT_DEV" "$MNT_DIR"

  pushd "$MNT_DIR" >/dev/null

  # Create /@ if missing
  if [ ! -d "@" ]; then
    log_info "Creating '/@' subvolume..."
    $SUDO btrfs subvolume create @
    
    log_info "Copying root files into '/@' using Btrfs reflinks..."
    for item in *; do
      if [[ "$item" != "@" && "$item" != "@home" && "$item" != "home" ]]; then
        $SUDO cp -a --reflink=always "$item" @/ 2>/dev/null || $SUDO cp -a --reflink=auto "$item" @/
      fi
    done
  fi

  # Create /@home if missing
  if [ ! -d "@home" ]; then
    log_info "Creating '/@home' subvolume..."
    $SUDO btrfs subvolume create @home
    
    if [ -d "home" ] && [ "$(ls -A home 2>/dev/null)" ]; then
      log_info "Copying user data into '/@home'..."
      $SUDO cp -a --reflink=always home/* @home/ 2>/dev/null || $SUDO cp -a --reflink=auto home/* @home/ 2>/dev/null || true
    fi
  fi

  # Create /@snapshots sibling subvolume on subvolid=5
  if [ ! -d "@snapshots" ]; then
    log_info "Creating '/@snapshots' sibling subvolume..."
    $SUDO btrfs subvolume create @snapshots
  fi

  # Update /etc/fstab inside the new @ subvolume
  log_info "Updating /etc/fstab inside '/@' subvolume..."
  $SUDO sed -i '/\s\/\s/ s/defaults/defaults,subvol=@/' "@/etc/fstab"
  
  if ! grep -q '/home' "@/etc/fstab"; then
    UUID=$(findmnt -n -o UUID /)
    echo "UUID=${UUID}  /home  btrfs  defaults,subvol=@home  0  0" | $SUDO tee -a "@/etc/fstab" >/dev/null
  else
    $SUDO sed -i '/\s\/home\s/ s/defaults/defaults,subvol=@home/' "@/etc/fstab"
  fi

  # Add /.snapshots mount backed by @snapshots
  if ! grep -q '/.snapshots' "@/etc/fstab"; then
    log_info "Adding '@snapshots' mountpoint to /etc/fstab..."
    echo "UUID=${UUID}  /.snapshots  btrfs  defaults,subvol=@snapshots,noatime  0  0" | $SUDO tee -a "@/etc/fstab" >/dev/null
  fi

  popd >/dev/null
  $SUDO umount "$MNT_DIR"
  rm -rf "$MNT_DIR"

  # Ensure target mountpoint exists and is mounted before Snapper setup
  $SUDO mkdir -p /.snapshots
  $SUDO mount /.snapshots 2>/dev/null || true

  log_warn "Subvolumes '/@', '/@home' and '/@snapshots' prepared. Reboot required to switch."
  exit 0
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

  # 4. Set mount point, filesystem type, and limits directly inside the config file
  $SUDO sed -i 's|^SUBVOLUME=.*|SUBVOLUME="/"|' /etc/snapper/configs/root
  $SUDO sed -i 's|^FSTYPE=.*|FSTYPE="btrfs"|' /etc/snapper/configs/root
  $SUDO sed -i 's|^NUMBER_MIN_AGE=.*|NUMBER_MIN_AGE="1800"|' /etc/snapper/configs/root
  $SUDO sed -i 's|^NUMBER_LIMIT=.*|NUMBER_LIMIT="10"|' /etc/snapper/configs/root
  $SUDO sed -i 's|^NUMBER_LIMIT_IMPORTANT=.*|NUMBER_LIMIT_IMPORTANT="5"|' /etc/snapper/configs/root
  $SUDO sed -i 's|^TIMELINE_CREATE=.*|TIMELINE_CREATE="no"|' /etc/snapper/configs/root
  $SUDO sed -i 's|^TIMELINE_CLEANUP=.*|TIMELINE_CLEANUP="yes"|' /etc/snapper/configs/root

  # 5. Lock permissions on /.snapshots
  $SUDO chmod 750 /.snapshots
  $SUDO chown root:root /.snapshots
}

# ------------------------------------------------------------------------------
# 4. ENABLE AUTOMATIC PRE/POST APT SNAPSHOTS
# ------------------------------------------------------------------------------
setup_apt_snapshots() {
  log_info "Configuring APT hooks for automatic system upgrade snapshots..."

  if [ -f "/etc/apt/apt.conf.d/80snapper" ]; then
    log_info "APT Snapper integration successfully active (/etc/apt/apt.conf.d/80snapper)."
  else
    log_info "Creating custom APT hook for Snapper..."
    cat <<'HOOK' | $SUDO tee /etc/apt/apt.conf.d/80snapper > /dev/null
// Automatic Snapper snapshots before and after APT operations
DPkg::Pre-Invoke { "if [ -x /usr/bin/snapper ]; then snapper -c root create -t pre -p -d 'APT Pre-Update Snapshot'; fi"; };
DPkg::Post-Invoke { "if [ -x /usr/bin/snapper ]; then snapper -c root create -t post --cleanup-algorithm=number -d 'APT Post-Update Snapshot'; fi"; };
HOOK
  fi
}

# ------------------------------------------------------------------------------
# 5. ENABLE SNAPPER CLEANUP TIMERS
# ------------------------------------------------------------------------------
enable_services() {
  log_info "Enabling Snapper automatic maintenance timers..."
  $SUDO systemctl daemon-reload
  $SUDO systemctl enable --now snapper-cleanup.timer
}

# ------------------------------------------------------------------------------
# MAIN EXECUTION
# ------------------------------------------------------------------------------
main() {
  install_packages

  ROOT_FSTYPE=$(findmnt -n -o FSTYPE /)
  if [[ "$ROOT_FSTYPE" != "btrfs" ]]; then
    log_error "Root filesystem '/' is currently '$ROOT_FSTYPE', not Btrfs."
    log_error "Snapper automated snapshots require a Btrfs root filesystem."
    exit 1
  fi

  migrate_subvolumes
  setup_snapper_root
  setup_apt_snapshots
  enable_services

  log_info "Btrfs and Snapper configuration complete!"
}

main "$@"
