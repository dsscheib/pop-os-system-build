#!/usr/bin/env bash
# Configure the secondary Samsung SSD 970 EVO NVMe drive: install PCIe/APST
# power-management udev rules and mount or format its @data/@snapshots Btrfs pool.
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

DEFAULT_DISK_BY_ID="/dev/disk/by-id/nvme-Samsung_SSD_970_EVO_500GB_S5H7NS0NB35276F"
UDEV_RULE_NAME="99-samsung-970-evo-nvme.rules"
MOUNT_DATA="/mnt/data"
MOUNT_SNAPSHOTS="/mnt/data/.snapshots"
BTRFS_OPTS_BASE="defaults,noatime,compress=zstd:1,ssd,discard=async,space_cache=v2,nofail"

usage() {
  die "Usage: sudo $0 [--format] [DISK]"
}

install_nvme_udev_rules() {
  local rule_src="$SCRIPT_DIR/resources/udev/$UDEV_RULE_NAME"
  local rule_dst="/etc/udev/rules.d/$UDEV_RULE_NAME"
  [[ -f "$rule_src" ]] || die "Missing udev rule resource: $rule_src"
  install -D -o root -g root -m 0644 "$rule_src" "$rule_dst"
  udevadm control --reload-rules
  udevadm trigger --subsystem-match=nvme --subsystem-match=pci
  udevadm settle
  log_info "Installed and triggered $rule_dst."
}

verify_nvme_power_settings() {
  local pci_dir nvme_dir val
  local found=0
  for pci_dir in /sys/bus/pci/devices/*; do
    [[ -f "$pci_dir/vendor" && -f "$pci_dir/device" ]] || continue
    if [[ $(<"$pci_dir/vendor") == "0x144d" && $(<"$pci_dir/device") == "0xa808" ]]; then
      found=1
      for attr in "link/l1_2_aspm:0" "link/l1_2_pcipm:0" "d3cold_allowed:0" "power/control:on"; do
        local name=${attr%%:*} expected=${attr##*:}
        if [[ -f "$pci_dir/$name" ]]; then
          val=$(<"$pci_dir/$name")
          [[ "$val" == "$expected" ]] || die "Expected $pci_dir/$name=$expected, got $val"
        fi
      done
      for nvme_dir in "$pci_dir"/nvme/nvme*; do
        [[ -d "$nvme_dir" ]] || continue
        if [[ -f "$nvme_dir/power/pm_qos_latency_tolerance_us" ]]; then
          val=$(<"$nvme_dir/power/pm_qos_latency_tolerance_us")
          [[ "$val" == "5500" ]] || die "Expected $nvme_dir/power/pm_qos_latency_tolerance_us=5500, got $val"
        fi
      done
    fi
  done
  (( found )) || log_warn "Samsung 970 EVO PCI controller (144d:a808) not currently detected on PCIe bus."
}

resolve_target_disk() {
  local explicit=${1:-} candidate
  if [[ -n "$explicit" ]]; then
    [[ -b "$explicit" ]] || die "Target block device does not exist: $explicit"
    readlink -f -- "$explicit"
    return
  fi
  if [[ -b "$DEFAULT_DISK_BY_ID" ]]; then
    readlink -f -- "$DEFAULT_DISK_BY_ID"
    return
  fi
  local -a matches=()
  for candidate in /sys/class/nvme/nvme*; do
    [[ -f "$candidate/device/vendor" && -f "$candidate/device/device" ]] || continue
    if [[ $(<"$candidate/device/vendor") == "0x144d" && $(<"$candidate/device/device") == "0xa808" ]]; then
      local blk
      for blk in "$candidate"/nvme*n1; do
        [[ -d "$blk" && -b "/dev/${blk##*/}" ]] && matches+=("/dev/${blk##*/}")
      done
    fi
  done
  (( ${#matches[@]} == 1 )) || die "Could not uniquely auto-detect Samsung 970 EVO disk; pass DISK explicitly."
  readlink -f -- "${matches[0]}"
}

verify_disk_is_secondary() {
  local disk=$1 mp src parent
  [[ $(lsblk -dnro TYPE "$disk" 2>/dev/null || true) == "disk" ]] || die "Target must be a whole disk block device: $disk"

  for mp in / /boot /boot/efi /recovery /home /.snapshots /swap; do
    src=$(findmnt -n -o SOURCE --mountpoint "$mp" 2>/dev/null || true)
    [[ -n "$src" ]] || continue
    src=${src%%\[*}
    parent=$(lsblk -dnro PKNAME "$src" 2>/dev/null || true)
    if [[ "$src" == "$disk" || ( -n "$parent" && "/dev/$parent" == "$disk" ) ]]; then
      die "Refusing to operate on system disk $disk (backing $mp)."
    fi
  done
}

unmount_existing_target_mounts() {
  local disk=$1 mp src parent
  for mp in "$MOUNT_SNAPSHOTS" "$MOUNT_DATA"; do
    if findmnt -n --mountpoint "$mp" >/dev/null 2>&1; then
      src=$(findmnt -n -o SOURCE --mountpoint "$mp")
      src=${src%%\[*}
      parent=$(lsblk -dnro PKNAME "$src" 2>/dev/null || true)
      if [[ "$src" == "$disk" || "/dev/$parent" == "$disk" ]]; then
        umount "$mp"
      else
        die "Mountpoint $mp is already mounted from another device ($src)."
      fi
    fi
  done

  local mounted
  mounted=$(lsblk -lnro MOUNTPOINT "$disk" | awk 'NF {print; exit}')
  [[ -z "$mounted" ]] || die "Disk $disk has active mounts ($mounted); unmount them first."
}

partition_and_format_disk() {
  local disk=$1 part
  log_info "Partitioning $disk with GPT and formatting Btrfs label 'data'..."
  parted -s -a optimal "$disk" mklabel gpt mkpart data btrfs 1MiB 100%
  partprobe "$disk"
  udevadm settle

  part=$(lsblk -lnro PATH,TYPE "$disk" | awk '$2 == "part" {print $1; exit}')
  [[ -n "$part" && -b "$part" ]] || die "Failed to locate newly created partition on $disk."
  mkfs.btrfs -f -L data "$part"
  udevadm trigger --name-match="${part##*/}"
  udevadm settle
  printf '%s\n' "$part"
}

locate_existing_btrfs_partition() {
  local disk=$1 parts fstype
  parts=$(lsblk -lnro PATH,TYPE "$disk" | awk '$2 == "part" {print $1}')
  [[ -n "$parts" && "$parts" != *$'\n'* ]] || die "Expected a single partition on $disk; pass --format to repartition and format."
  fstype=$(blkid -s TYPE -o value "$parts" 2>/dev/null || true)
  [[ "$fstype" == "btrfs" ]] || die "Partition $parts is not Btrfs (detected '${fstype:-none}'); pass --format to initialize it."
  printf '%s\n' "$parts"
}

ensure_subvolumes() {
  local part=$1 top_mnt="$WORK_DIR/top"
  mkdir -p "$top_mnt"
  mount -t btrfs -o subvolid=5 "$part" "$top_mnt"
  trap 'umount "$top_mnt" 2>/dev/null || true' RETURN
  if ! btrfs subvolume show "$top_mnt/@data" >/dev/null 2>&1; then
    btrfs subvolume create "$top_mnt/@data" >/dev/null
    log_info "Created Btrfs subvolume @data."
  fi
  if ! btrfs subvolume show "$top_mnt/@snapshots" >/dev/null 2>&1; then
    btrfs subvolume create "$top_mnt/@snapshots" >/dev/null
    log_info "Created Btrfs subvolume @snapshots."
  fi
  umount "$top_mnt"
  trap - RETURN
}

render_secondary_fstab() {
  local fstab=$1 uuid=$2
  awk -v uuid="$uuid" -v mnt_data="$MOUNT_DATA" -v mnt_snap="$MOUNT_SNAPSHOTS" -v base_opts="$BTRFS_OPTS_BASE" '
    BEGIN { OFS = "\t" }
    /^[[:space:]]*#/ || /^[[:space:]]*$/ { print; next }
    $2 == mnt_data || $2 == mnt_snap { next }
    { print }
    END {
      print "UUID=" uuid, mnt_data, "btrfs", base_opts ",subvol=@data", 0, 0
      print "UUID=" uuid, mnt_snap, "btrfs", base_opts ",subvol=@snapshots", 0, 0
    }
  ' "$fstab"
}

update_fstab_and_mount() {
  local part=$1 target_user=$2 uuid rendered backup
  uuid=$(blkid -s UUID -o value "$part")
  [[ -n "$uuid" ]] || die "Could not determine Btrfs UUID for $part."

  rendered="$WORK_DIR/fstab"
  render_secondary_fstab /etc/fstab "$uuid" > "$rendered"

  if ! cmp -s "$rendered" /etc/fstab; then
    if ! compgen -G "/etc/fstab.pre-secondary-btrfs.*" >/dev/null; then
      backup=$(mktemp /etc/fstab.pre-secondary-btrfs.XXXXXX)
      cp -a /etc/fstab "$backup"
      log_info "Backed up /etc/fstab to $backup."
    fi
    install -o root -g root -m 0644 "$rendered" /etc/fstab
  fi

  mkdir -p "$MOUNT_DATA"
  systemctl daemon-reload
  if ! findmnt -n --mountpoint "$MOUNT_DATA" >/dev/null 2>&1; then
    mount "$MOUNT_DATA"
  fi
  mkdir -p "$MOUNT_SNAPSHOTS"
  if ! findmnt -n --mountpoint "$MOUNT_SNAPSHOTS" >/dev/null 2>&1; then
    mount "$MOUNT_SNAPSHOTS"
  fi

  [[ $(findmnt -n -o FSTYPE --mountpoint "$MOUNT_DATA") == btrfs &&
     $(findmnt -n -o FSROOT --mountpoint "$MOUNT_DATA") == /@data &&
     $(findmnt -n -o UUID --mountpoint "$MOUNT_DATA") == "$uuid" ]] || die "Failed to verify $MOUNT_DATA mount."
  [[ $(findmnt -n -o FSTYPE --mountpoint "$MOUNT_SNAPSHOTS") == btrfs &&
     $(findmnt -n -o FSROOT --mountpoint "$MOUNT_SNAPSHOTS") == /@snapshots &&
     $(findmnt -n -o UUID --mountpoint "$MOUNT_SNAPSHOTS") == "$uuid" ]] || die "Failed to verify $MOUNT_SNAPSHOTS mount."

  if [[ -n "$target_user" ]] && id -u "$target_user" >/dev/null 2>&1; then
    local uid gid user_home
    uid=$(id -u "$target_user")
    gid=$(id -g "$target_user")
    chown "$uid:$gid" "$MOUNT_DATA" "$MOUNT_SNAPSHOTS"
    chmod 0755 "$MOUNT_DATA" "$MOUNT_SNAPSHOTS"
    user_home=$(getent passwd "$target_user" | cut -d: -f6)
    if [[ -n "$user_home" && -d "$user_home" ]]; then
      if [[ -L "$user_home/data" || ! -e "$user_home/data" ]]; then
        ln -sfn "$MOUNT_DATA" "$user_home/data"
        chown -h "$uid:$gid" "$user_home/data"
      else
        log_warn "Skipping symlink $user_home/data because a non-symlink path already exists."
      fi
    fi
  fi
}

main() {
  local do_format=0 explicit_disk=""
  while (( $# > 0 )); do
    case "$1" in
      --format) do_format=1; shift ;;
      -h|--help) usage ;;
      -*) usage ;;
      *)
        [[ -z "$explicit_disk" ]] || usage
        explicit_disk=$1
        shift
        ;;
    esac
  done

  if [[ $EUID -ne 0 ]]; then
    local -a args=()
    (( do_format )) && args+=(--format)
    [[ -n "$explicit_disk" ]] && args+=("$explicit_disk")
    exec sudo -- "$SCRIPT_DIR/${BASH_SOURCE[0]##*/}" "${args[@]}"
  fi

  require_commands awk blkid btrfs cmp findmnt getent id install ln lsblk mkfs.btrfs mount parted partprobe readlink systemctl udevadm umount
  start_workspace secondary-btrfs

  install_nvme_udev_rules
  verify_nvme_power_settings

  local disk part target_user=${SUDO_USER:-}
  disk=$(resolve_target_disk "$explicit_disk")
  verify_disk_is_secondary "$disk"
  unmount_existing_target_mounts "$disk"

  if (( do_format )); then
    part=$(partition_and_format_disk "$disk")
  else
    part=$(locate_existing_btrfs_partition "$disk")
  fi

  ensure_subvolumes "$part"
  update_fstab_and_mount "$part" "$target_user"
  log_info "Secondary Btrfs drive ($part) is configured and mounted at $MOUNT_DATA."
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then main "$@"; fi
