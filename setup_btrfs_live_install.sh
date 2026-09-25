#!/usr/bin/env bash

# ==============================================================================
# BTRFS SUBVOLUME SETUP (RUN FROM LIVE USB BEFORE FIRST REBOOT)
# Targets: Target drive mounted from Pop!_OS Live Installer
# ==============================================================================

set -euo pipefail

log_info()  { echo -e "\033[0;34m[INFO]\033[0m $1"; }
log_warn()  { echo -e "\033[0;33m[WARN]\033[0m $1"; }
log_error() { echo -e "\033[0;31m[ERROR]\033[0m $1"; }

if [[ $EUID -ne 0 ]]; then
  log_error "This script must be run as root in the Live USB environment (sudo)."
  exit 1
fi

TARGET_DEV=${1:-""}

if [[ -z "$TARGET_DEV" ]]; then
  log_error "Usage: sudo ./setup_btrfs_live_install.sh /dev/sdXN (or /dev/nvme0n1pN)"
  exit 1
fi

MNT="/mnt/target_btrfs"

# Clean up any existing mount points from previous attempts
if mountpoint -q "$MNT"; then
  log_warn "$MNT is already mounted. Cleaning up previous mounts..."
  umount -R "$MNT" 2>/dev/null || umount -l "$MNT" 2>/dev/null || true
fi

mkdir -p "$MNT"

log_info "Mounting top-level Btrfs volume ID 5 from $TARGET_DEV..."
mount -o subvolid=5 "$TARGET_DEV" "$MNT"

# Ensure we return to working directory on script exit/error
trap 'popd >/dev/null 2>&1 || true' EXIT
pushd "$MNT" >/dev/null

log_info "Creating subvolumes '/@', '/@home', and '/@snapshots'..."
btrfs subvolume create @ 2>/dev/null || true
btrfs subvolume create @home 2>/dev/null || true
btrfs subvolume create @snapshots 2>/dev/null || true

log_info "Restructuring root and home directories into subvolumes..."
# Move existing root files into @
for item in *; do
  if [[ "$item" != "@" && "$item" != "@home" && "$item" != "@snapshots" && "$item" != "home" ]]; then
    mv "$item" @/
  fi
done

# Move home data into @home if present
if [ -d "home" ] && [ "$(ls -A home 2>/dev/null)" ]; then
  mv home/* @home/ 2>/dev/null || true
fi

# Remove top-level flat home directory
rm -rf home

log_info "Updating /etc/fstab inside target subvolume..."
sed -i '/\s\/\s/ s/defaults/defaults,subvol=@/' "@/etc/fstab"

ROOT_UUID=$(blkid -s UUID -o value "$TARGET_DEV")
if ! grep -q '/home' "@/etc/fstab"; then
  echo "UUID=${ROOT_UUID}  /home  btrfs  defaults,subvol=@home  0  0" >> "@/etc/fstab"
else
  sed -i '/\s\/home\s/ s/defaults.*/defaults,subvol=@home  0  0/' "@/etc/fstab"
fi

# Add /.snapshots mount entry backing the @snapshots subvolume
if ! grep -q '/.snapshots' "@/etc/fstab"; then
  echo "UUID=${ROOT_UUID}  /.snapshots  btrfs  defaults,subvol=@snapshots,noatime  0  0" >> "@/etc/fstab"
fi

# Leave directory before unmounting
popd >/dev/null
trap - EXIT

log_info "Unmounting top-level volume ID 5..."
umount "$MNT" || umount -l "$MNT"

log_info "Mounting new subvolumes..."
mount -o subvol=@ "$TARGET_DEV" "$MNT"

# Ensure target mount point exists for /home before mounting
mkdir -p "$MNT/home"
mount -o subvol=@home "$TARGET_DEV" "$MNT/home"

# Create target /.snapshots directory and mount @snapshots before chroot
mkdir -p "$MNT/.snapshots"
mount -o subvol=@snapshots "$TARGET_DEV" "$MNT/.snapshots"

# Bind mount API filesystems for chroot
for dir in /dev /dev/pts /proc /sys /run; do
  mkdir -p "$MNT$dir"
  mount --bind "$dir" "$MNT$dir"
done

# Mount EFI variables if system is booted in UEFI mode
if [ -d "/sys/firmware/efi/efivars" ]; then
  mkdir -p "$MNT/sys/firmware/efi/efivars"
  mount --bind /sys/firmware/efi/efivars "$MNT/sys/firmware/efi/efivars" 2>/dev/null || true
fi

# Locate the EFI System Partition UUID from disk
PARENT_DISK=$(echo "$TARGET_DEV" | sed -E 's/p?[0-9]+$//')
EFI_DEV=""

for part in $(ls "${PARENT_DISK}"* 2>/dev/null); do
  if blkid "$part" | grep -q 'TYPE="vfat"'; then
    EFI_DEV="$part"
    break
  fi
done

if [[ -n "$EFI_DEV" && -b "$EFI_DEV" ]]; then
  EFI_UUID=$(blkid -s UUID -o value "$EFI_DEV")
  log_info "Found EFI System Partition ($EFI_DEV) with UUID: $EFI_UUID"
  
  mkdir -p "$MNT/boot/efi"
  mount "$EFI_DEV" "$MNT/boot/efi"

  # Ensure fstab inside chroot reflects the real EFI UUID
  if ! grep -q '/boot/efi' "$MNT/etc/fstab"; then
    echo "UUID=${EFI_UUID}  /boot/efi  vfat  umask=0077  0  1" >> "$MNT/etc/fstab"
  fi
else
  log_warn "Could not auto-detect EFI partition on $PARENT_DISK."
fi

log_info "Configuring kernelstub rootflags inside target chroot..."
chroot "$MNT" /bin/bash -c "mount -a 2>/dev/null || true; kernelstub -a 'rootflags=subvol=@'" || log_warn "kernelstub warning, verify boot options manually."

log_info "Unmounting target filesystems..."
umount -R "$MNT" 2>/dev/null || umount -l "$MNT" 2>/dev/null || true
rm -rf "$MNT"

log_info "Success! Btrfs subvolumes /@ and /@home are prepared."
log_info "You may now safely reboot into your new Pop!_OS installation."
