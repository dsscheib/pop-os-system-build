#!/usr/bin/env bash

# ==============================================================================
# BTRFS SUBVOLUME SETUP (RUN FROM LIVE USB BEFORE FIRST REBOOT)
# Targets: Target drive mounted from Pop!_OS Live Installer
# ==============================================================================

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

if [[ $EUID -ne 0 ]]; then
  log_error "This script must be run as root in the Live USB environment (sudo)."
  exit 1
fi

TARGET_DEV=${1:-""}

if [[ -z "$TARGET_DEV" || ! -b "$TARGET_DEV" ]]; then
  log_error "Usage: sudo ./setup_btrfs_live_install.sh /dev/sdXN (or /dev/nvme0n1pN)"
  log_error "Provided target device '$TARGET_DEV' is invalid or not a block device."
  exit 1
fi

MNT="/mnt/target_btrfs"

# Cleanup trap: safely unmount without risking recursive deletion on an active mount
cleanup() {
  local exit_code=$?
  if mountpoint -q "$MNT" 2>/dev/null; then
    log_warn "Unmounting mounted filesystems at $MNT..."
    umount -R "$MNT" 2>/dev/null || true
  fi

  # Security check: Never run rm -rf if the mountpoint is still active!
  if mountpoint -q "$MNT" 2>/dev/null; then
    log_error "CRITICAL: $MNT is still mounted! Refusing to run rm -rf to prevent data loss."
  else
    rmdir "$MNT" 2>/dev/null || rm -rf "$MNT" 2>/dev/null || true
  fi
  exit "$exit_code"
}
trap cleanup EXIT

# Clean up any existing mount points from previous attempts
if mountpoint -q "$MNT" 2>/dev/null; then
  log_warn "$MNT is already mounted. Cleaning up previous mounts..."
  umount -R "$MNT" 2>/dev/null || true
fi

mkdir -p "$MNT"

log_info "Mounting top-level Btrfs volume ID 5 from $TARGET_DEV..."
mount -o subvolid=5 "$TARGET_DEV" "$MNT"

log_info "Creating subvolumes '/@', '/@home', and '/@snapshots'..."
btrfs subvolume create "$MNT/@" 2>/dev/null || true
btrfs subvolume create "$MNT/@home" 2>/dev/null || true
btrfs subvolume create "$MNT/@snapshots" 2>/dev/null || true

log_info "Restructuring root and home directories into subvolumes..."
(
  cd "$MNT"
  shopt -s nullglob dotglob
  for item in *; do
    case "$item" in
      .|..|@|@home|@snapshots|home)
        continue
        ;;
      *)
        mv "$item" @/
        ;;
    esac
  done

  # Move home data (including dotfiles) into @home if present
  if [ -d "home" ]; then
    for uitem in home/*; do
      case "$uitem" in
        home/.|home/..)
          continue
          ;;
        *)
          mv "$uitem" @home/ 2>/dev/null || true
          ;;
      esac
    done
    rm -rf home
  fi
  shopt -u dotglob nullglob
)

log_info "Updating /etc/fstab inside target subvolume..."
ROOT_UUID=$(blkid -s UUID -o value "$TARGET_DEV")

# Ensure root mount references subvol=@
if grep -qE '\s/\s' "$MNT/@/etc/fstab"; then
  sed -i -E '/\s\/\s/ s/(\bdefaults\b[^\s]*|\bsubvolid=[0-9]+)/defaults,subvol=@/' "$MNT/@/etc/fstab"
fi

# Ensure /home mount entry exists
if ! grep -qE '\s/home\s' "$MNT/@/etc/fstab"; then
  echo "UUID=${ROOT_UUID}  /home  btrfs  defaults,subvol=@home  0  0" >> "$MNT/@/etc/fstab"
else
  sed -i -E '/\s\/home\s/ s/defaults.*/defaults,subvol=@home  0  0/' "$MNT/@/etc/fstab"
fi

# Ensure /.snapshots mount entry exists
if ! grep -qE '\s/\.snapshots\s' "$MNT/@/etc/fstab"; then
  echo "UUID=${ROOT_UUID}  /.snapshots  btrfs  defaults,subvol=@snapshots,noatime  0  0" >> "$MNT/@/etc/fstab"
fi

# ------------------------------------------------------------------------------
# ROBUST EFI RESOLUTION (Prevents /recovery collision)
# ------------------------------------------------------------------------------
locate_efi_partition() {
  local target_mnt="$1"
  local target_dev="$2"
  local efi_dev=""

  # 1. First, inspect target fstab populated by the Pop!_OS installer (highest confidence)
  if [[ -f "$target_mnt/@/etc/fstab" ]]; then
    local fstab_efi
    fstab_efi=$(awk '$2=="/boot/efi" {print $1}' "$target_mnt/@/etc/fstab" 2>/dev/null || true)
    if [[ -n "$fstab_efi" ]]; then
      if [[ "$fstab_efi" =~ ^UUID=(.*) ]]; then
        efi_dev=$(blkid -U "${BASH_REMATCH[1]}" 2>/dev/null || true)
      elif [[ -b "$fstab_efi" ]]; then
        efi_dev="$fstab_efi"
      fi
    fi
  fi

  # 2. Query GPT Partition Type GUID for ESP (c12a7328-f81f-11d2-ba4b-00a0c93ec93b)
  if [[ -z "$efi_dev" && -b "$target_dev" ]] && command -v lsblk &>/dev/null; then
    local parent_disk
    parent_disk=$(lsblk -no PKNAME "$target_dev" 2>/dev/null | head -n 1 || true)
    if [[ -n "$parent_disk" ]]; then
      efi_dev=$(lsblk -rno PATH,PARTTYPE "/dev/$parent_disk" 2>/dev/null | \
        awk '$2=="c12a7328-f81f-11d2-ba4b-00a0c93ec93b"{print $1; exit}')
    fi
  fi

  # 3. Match label EFI while strictly avoiding RECOVERY
  if [[ -z "$efi_dev" && -n "${parent_disk:-}" ]]; then
    efi_dev=$(lsblk -rno PATH,LABEL "/dev/$parent_disk" 2>/dev/null | \
      awk '$2=="EFI"{print $1; exit}')
  fi

  echo "$efi_dev"
}

EFI_DEV=$(locate_efi_partition "$MNT" "$TARGET_DEV")

log_info "Unmounting top-level volume ID 5..."
umount "$MNT" || true

log_info "Mounting new subvolumes for configuration..."
mount -o subvol=@ "$TARGET_DEV" "$MNT"

mkdir -p "$MNT/home"
mount -o subvol=@home "$TARGET_DEV" "$MNT/home"

mkdir -p "$MNT/.snapshots"
mount -o subvol=@snapshots "$TARGET_DEV" "$MNT/.snapshots"

# Bind mount API filesystems for chroot
for dir in /dev /dev/pts /proc /sys /run; do
  mkdir -p "$MNT$dir"
  mount --bind "$dir" "$MNT$dir"
done

# Mount EFI variables if booted in UEFI mode
if [ -d "/sys/firmware/efi/efivars" ]; then
  mkdir -p "$MNT/sys/firmware/efi/efivars"
  mount --bind /sys/firmware/efi/efivars "$MNT/sys/firmware/efi/efivars" 2>/dev/null || true
fi

if [[ -n "$EFI_DEV" && -b "$EFI_DEV" ]]; then
  EFI_UUID=$(blkid -s UUID -o value "$EFI_DEV")
  log_info "Verified EFI System Partition ($EFI_DEV) with UUID: $EFI_UUID"
  
  mkdir -p "$MNT/boot/efi"
  mount "$EFI_DEV" "$MNT/boot/efi"

  if ! grep -qE '\s/boot/efi\s' "$MNT/etc/fstab"; then
    echo "UUID=${EFI_UUID}  /boot/efi  vfat  umask=0077  0  1" >> "$MNT/etc/fstab"
  fi
else
  log_warn "Could not uniquely determine EFI System Partition. Please verify /boot/efi manually."
fi

log_info "Configuring kernelstub and initramfs inside target chroot..."
chroot "$MNT" /bin/bash -c "
  mount -a 2>/dev/null || true
  if ! kernelstub -p 2>/dev/null | grep -q 'rootflags=subvol=@'; then
    kernelstub -a 'rootflags=subvol=@'
  fi
  update-initramfs -u -k all
" || log_warn "kernelstub warning, verify boot options manually."

log_info "Unmounting target filesystems..."
trap - EXIT
umount -R "$MNT" 2>/dev/null || true

if mountpoint -q "$MNT" 2>/dev/null; then
  log_error "Warning: $MNT could not be fully unmounted. Leaving mountpoint directory intact."
else
  rmdir "$MNT" 2>/dev/null || rm -rf "$MNT" 2>/dev/null || true
fi

log_info "Success! Btrfs subvolumes /@ and /@home are prepared."
log_info "You may now safely reboot into your new Pop!_OS installation."
