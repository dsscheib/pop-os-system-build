#!/usr/bin/env bash
# Run from a UEFI live USB after installation, before the first reboot.
# Only a fresh installation directly in Btrfs top-level subvolume ID 5 is supported.
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

fail() { log_error "$1" >&2; exit 1; }

# Rewrite active entries by field, retaining comments and unrelated mount options.
# Output goes to stdout so callers can validate before replacing the original.
render_fstab() {
  local fstab=$1 root_uuid=$2 efi_uuid=$3
  awk -v root_uuid="$root_uuid" -v efi_uuid="$efi_uuid" '
    function options(old, selector,    n, parts, i, out) {
      n = split(old, parts, ",")
      for (i = 1; i <= n; i++) {
        if (parts[i] == "" || parts[i] ~ /^subvol(id)?=/) continue
        out = out (out == "" ? "" : ",") parts[i]
      }
      return out (out == "" ? "" : ",") "subvol=" selector
    }
    BEGIN { OFS = "\t" }
    /^[[:space:]]*#/ || /^[[:space:]]*$/ { print; next }
    {
      if (NF < 4) { print "Malformed fstab entry at line " NR > "/dev/stderr"; bad = 1; next }
      original = $0
      comment = ""
      for (i = 1; i <= NF; i++) {
        if ($i ~ /^#/) {
          comment = $i
          for (j = i + 1; j <= NF; j++) comment = comment " " $j
          NF = i - 1
          break
        }
      }
      if (NF < 4 || NF > 6 || (NF >= 5 && $5 !~ /^[0-9]+$/) || (NF >= 6 && $6 !~ /^[0-9]+$/)) {
        print "Malformed fstab fields at line " NR > "/dev/stderr"; bad = 1; next
      }
      point = $2
      if (++seen[point] > 1 && point != "none" && point != "swap") {
        print "Duplicate fstab mountpoint: " point > "/dev/stderr"; bad = 1
      }
      if (point == "/" || point == "/home" || point == "/.snapshots") {
        selector = point == "/" ? "@" : (point == "/home" ? "@home" : "@snapshots")
        $1 = "UUID=" root_uuid; $3 = "btrfs"; $4 = options($4, selector)
        # Replaced fields always include both numeric trailing fields.
        $5 = 0; $6 = 0
        print $0 (comment == "" ? "" : "\t" comment)
      } else if (point == "/boot/efi") {
        $1 = "UUID=" efi_uuid; $3 = "vfat"
        $5 = 0; $6 = 1
        print $0 (comment == "" ? "" : "\t" comment)
      } else print original
    }
    END {
      if (!seen["/"]) { print "Missing root fstab entry" > "/dev/stderr"; bad = 1 }
      if (bad) exit 1
      if (!seen["/home"]) print "UUID=" root_uuid, "/home", "btrfs", "defaults,subvol=@home", 0, 0
      if (!seen["/.snapshots"]) print "UUID=" root_uuid, "/.snapshots", "btrfs", "defaults,noatime,subvol=@snapshots", 0, 0
      if (!seen["/boot/efi"]) print "UUID=" efi_uuid, "/boot/efi", "vfat", "umask=0077", 0, 1
    }
  ' "$fstab"
}

# Normalize rootflags while retaining other kernel options and root mount flags.
render_kernel_options() {
  python3 - "$1" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    config = json.load(stream)
effective = dict(config.get("default", {}))
effective.update(config.get("user", {}))
options = effective.get("kernel_options")
if not isinstance(options, list) or not all(isinstance(x, str) for x in options):
    raise SystemExit("Invalid kernelstub kernel_options")
kept, flags = [], []
for option in options:
    if option.startswith("rootflags="):
        flags.extend(flag for flag in option.split("=", 1)[1].split(",")
                     if flag and not flag.startswith(("subvol=", "subvolid=")))
    else:
        kept.append(option)
kept.append("rootflags=" + ",".join(dict.fromkeys(flags + ["subvol=@"])))
print(" ".join(kept))
PY
}

resolve_source() {
  case "$1" in
    UUID=*|LABEL=*|PARTUUID=*|PARTLABEL=*) findfs "$1" ;;
    /dev/*) readlink -f -- "$1" ;;
    *) log_error "Unsupported filesystem source: $1" >&2; return 1 ;;
  esac
}

locate_efi_partition() {
  local fstab=$1 target_dev=$2 explicit=${3:-} source parent candidates
  if [[ -n "$explicit" ]]; then
    readlink -f -- "$explicit"
    return
  fi
  source=$(awk '$0 !~ /^[[:space:]]*#/ && $2 == "/boot/efi" {print $1}' "$fstab")
  if [[ -n "$source" ]]; then
    [[ "$source" != *$'\n'* ]] || { log_error "Duplicate EFI entries." >&2; return 1; }
    resolve_source "$source"
    return
  fi
  parent=$(lsblk -dnro PKNAME "$target_dev")
  [[ -n "$parent" && "$parent" != *$'\n'* ]] || {
    log_error "Specify the EFI device explicitly for this device layout." >&2; return 1;
  }
  candidates=$(lsblk -rno PATH,PARTTYPE "/dev/$parent" |
    awk 'tolower($2) == "c12a7328-f81f-11d2-ba4b-00a0c93ec93b" {print $1}')
  [[ -n "$candidates" && "$candidates" != *$'\n'* ]] || {
    log_error "No unique EFI partition found; supply an explicit EFI device." >&2; return 1;
  }
  printf '%s\n' "$candidates"
}

# This directory is unique to this invocation. Never recursively delete it.
cleanup() {
  local status=$?
  trap - EXIT
  if [[ -n "${MNT:-}" ]]; then
    if mountpoint -q "$MNT"; then
      if ! umount -R "$MNT"; then
        log_error "Unmount failed; leaving $MNT intact." >&2
        status=1
      fi
    fi
    if ! rmdir -- "$MNT"; then
      log_error "Leaving mount directory $MNT intact for inspection." >&2
      status=1
    fi
  fi
  if [[ -n "${WORK_DIR:-}" ]]; then
    if mountpoint -q "$WORK_DIR/efi"; then
      if ! umount "$WORK_DIR/efi"; then
        log_error "Leaving EFI inspection mount at $WORK_DIR/efi intact." >&2
        status=1
      fi
    fi
    if [[ -d "$WORK_DIR/efi" ]]; then
      rmdir -- "$WORK_DIR/efi" || status=1
    fi
    # Only known temporary files in our private work directory are removed.
    rm -f -- "$WORK_DIR/fstab" || status=1
    rmdir -- "$WORK_DIR" || status=1
  fi
  if (( status != 0 )) && [[ ${MIGRATION_STARTED:-0} == 1 ]]; then
    log_error "Conversion did not complete. Do not reboot or rerun blindly: inspect the target via subvolid=5 and repair the partial layout and boot configuration first." >&2
  fi
  exit "$status"
}

copy_directory_metadata() {
  python3 - "$1" "$2" <<'PYTHON'
import os
import sys
source, destination = sys.argv[1:]
info = os.stat(source)
os.chown(destination, info.st_uid, info.st_gid)
os.chmod(destination, info.st_mode & 0o7777)
for name in os.listxattr(source):
    os.setxattr(destination, name, os.getxattr(source, name))
os.utime(destination, ns=(info.st_atime_ns, info.st_mtime_ns))
PYTHON
}

# Moves are deliberately fatal; rmdir only removes an empty source home directory.
migrate_layout() (
  local target=$1 item
  cd "$target"
  shopt -s nullglob dotglob
  for item in *; do
    case "$item" in @|@home|@snapshots|home) continue ;; esac
    mv -T -- "$item" "@/$item"
  done
  if [[ -d home ]]; then
    copy_directory_metadata home @home
    for item in home/*; do
      mv -T -- "$item" "@home/${item#home/}"
    done
    rmdir -- home
  fi
)

verify_boot_entry() {
  local esp=$1 uuid=$2 entry="$1/loader/entries/Pop_OS-current.conf" image images
  [[ -s "$entry" ]] || { log_error "Missing current Pop!_OS boot entry." >&2; return 1; }
  awk -v root="root=UUID=$uuid" '
    $1 == "options" {
      lines++
      for (i = 2; i <= NF; i++) {
        if ($i ~ /^root=/) { roots++; if ($i == root) correct_root++ }
        if ($i ~ /^rootflags=/) {
          rootflags++
          n = split(substr($i, 11), flags, ",")
          for (j = 1; j <= n; j++) {
            if (flags[j] ~ /^subvol(id)?=/) selectors++
            if (flags[j] == "subvol=@") correct_selector++
          }
        }
      }
    }
    END { exit !(lines == 1 && roots == 1 && correct_root == 1 && rootflags == 1 && selectors == 1 && correct_selector == 1) }
  ' "$entry" || { log_error "Boot entry has incorrect root device or subvolume options." >&2; return 1; }
  images=$(awk '$1 == "linux" || $1 == "initrd" {print $2}' "$entry")
  [[ $(awk '$1 == "linux" {n++} END {print n+0}' "$entry") == 1 &&
     $(awk '$1 == "initrd" {n++} END {print n+0}' "$entry") -ge 1 ]] || return 1
  while IFS= read -r image; do
    [[ -s "$esp/${image#/}" ]] || { log_error "Missing boot image: $image" >&2; return 1; }
  done <<< "$images"
}

configure_boot() {
  # Mount only the required filesystems in main; mount -a could mount unrelated disks.
  # shellcheck disable=SC2016 # $1 is the positional argument inside the chroot.
  chroot "$1" /bin/bash -euo pipefail -c '
    kernelstub -e /boot/efi -l -f -o "$1"
    # Install in the target, after setting boot options so package-triggered
    # initramfs updates also use the converted root layout.
    if ! command -v btrfs >/dev/null || ! command -v fsck.btrfs >/dev/null; then
      apt-get update
      DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends --reinstall btrfs-progs
      command -v btrfs >/dev/null
      command -v fsck.btrfs >/dev/null
    fi
    update-initramfs -u -k all
    kernelstub -e /boot/efi -l -f
  ' bash "$2"
}

verify_boot_selection() {
  local selection
  selection=$(awk '$1 == "default" {print $2}' "$1/loader/loader.conf") || return
  [[ $selection == Pop_OS-current || $selection == Pop_OS-current.conf ]] || {
    log_error "The default bootloader entry must select Pop_OS-current." >&2
    return 1
  }
}

is_pop_installation() {
  # Resolve os-release inside the chroot, including valid relative/absolute links.
  chroot "$1" /bin/cat /etc/os-release | grep -qE '^ID=(pop|"pop")$'
}

verify_repair_layout() {
  local path
  for path in @ @home @snapshots; do
    [[ -d "$1/$path" && ! -L "$1/$path" ]] || fail "Boot repair requires an existing $path subvolume."
    btrfs subvolume show "$1/$path" >/dev/null || fail "Boot repair requires $path to be a Btrfs subvolume."
  done
  # Leftover top-level data indicates an interrupted move; never hide it by
  # mounting @ and treating the conversion as complete.
  local -a entries
  shopt -s nullglob dotglob
  entries=("$1"/*)
  shopt -u nullglob dotglob
  for path in "${entries[@]}"; do
    case ${path##*/} in
      @|@home|@snapshots) ;;
      *) fail "Incomplete conversion: top-level ${path##*/} remains. Inspect the layout manually before boot repair." ;;
    esac
  done
}

verify_target_bootloader() {
  # is-installed inspects the target ESP. set-default instead requires the
  # currently booted loader's EFI interface, which a live USB may not provide.
  chroot "$1" bootctl --esp-path=/boot/efi is-installed
}

main() {
  [[ $EUID -eq 0 ]] || fail "Run this script as root from the live USB (sudo)."
  local repair_boot=0
  if [[ ${1:-} == --repair-boot ]]; then
    repair_boot=1
    shift
  fi
  [[ $# -ge 1 && $# -le 2 ]] || fail "Usage: sudo $SCRIPT_DIR/setup_btrfs_live_install.sh [--repair-boot] ROOT_DEVICE [EFI_DEVICE]"
  local tool target_dev efi_dev root_uuid efi_uuid subvolumes kernel_options path
  for tool in btrfs blkid lsblk findfs findmnt mount umount mountpoint chroot python3 awk mktemp readlink mv cp chmod chown rmdir rm grep mkdir cat du df; do
    command -v "$tool" >/dev/null || fail "Required command missing: $tool"
  done
  target_dev=$(readlink -f -- "$1")
  [[ -b "$target_dev" ]] || fail "Invalid root block device: $1"
  [[ $(blkid -s TYPE -o value "$target_dev") == btrfs ]] || fail "Root device must contain Btrfs."
  root_uuid=$(blkid -s UUID -o value "$target_dev")
  [[ -n "$root_uuid" ]] || fail "Root filesystem UUID is missing."
  [[ -d /sys/firmware/efi/efivars ]] || fail "Boot the live USB in UEFI mode."
  if findmnt -rn --source "UUID=$root_uuid" >/dev/null; then
    fail "Root filesystem is already mounted. Unmount its installer mounts before continuing."
  fi

  MNT=$(mktemp -d /mnt/pop-btrfs.XXXXXX)
  WORK_DIR=""
  MIGRATION_STARTED=0
  trap cleanup EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  WORK_DIR=$(mktemp -d /tmp/pop-btrfs.XXXXXX)
  log_info "Inspecting top-level Btrfs volume on $target_dev..."
  mount -t btrfs -o ro,subvolid=5 "$target_dev" "$MNT"
  mount --make-private "$MNT"
  if (( repair_boot )); then
    verify_repair_layout "$MNT"
    umount "$MNT"
    mount -t btrfs -o ro,subvol=@ "$target_dev" "$MNT"
    mount --make-private "$MNT"
  else
    subvolumes=$(btrfs subvolume list -o "$MNT")
    [[ -z "$subvolumes" ]] || fail "Existing Btrfs subvolumes detected. Use --repair-boot only for a completed conversion; inspect interrupted conversions manually."
    for path in @ @home @snapshots; do
      [[ ! -e "$MNT/$path" && ! -L "$MNT/$path" ]] || fail "Existing $path path detected; refusing to overwrite it."
    done
  fi
  for path in etc etc/fstab etc/kernelstub etc/kernelstub/configuration boot; do
    [[ -e "$MNT/$path" && ! -L "$MNT/$path" ]] || fail "Missing or symlinked target installation path: $path"
  done
  is_pop_installation "$MNT" || fail "Target is not a Pop!_OS installation."
  [[ ! -L "$MNT/home" && ( ! -e "$MNT/home" || -d "$MNT/home" ) ]] || fail "Unsupported home layout."
  if (( ! repair_boot )); then
    [[ ! -e "$MNT/.snapshots" && ! -L "$MNT/.snapshots" ]] || fail "Existing .snapshots data or layout detected; migrate it manually."
  else
    [[ -d "$MNT/.snapshots" && ! -L "$MNT/.snapshots" ]] || fail "Unsupported snapshots mountpoint."
  fi
  for path in boot/efi dev proc sys run; do
    [[ ! -L "$MNT/$path" && ( ! -e "$MNT/$path" || -d "$MNT/$path" ) ]] || fail "Unsupported mountpoint path: $path"
  done
  if awk '$0 !~ /^[[:space:]]*#/ && $2 == "/boot" {found=1} END {exit !found}' "$MNT/etc/fstab"; then
    fail "Separate /boot filesystems are unsupported by this conversion."
  fi
  # A separate home partition would be hidden by @home; never silently replace it.
  local home_source
  home_source=$(awk '$0 !~ /^[[:space:]]*#/ && $2 == "/home" {print $1}' "$MNT/etc/fstab")
  if [[ -n "$home_source" ]]; then
    [[ "$home_source" != *$'\n'* ]] || fail "Duplicate home fstab entries."
    [[ $(resolve_source "$home_source") -ef "$target_dev" ]] || fail "Separate home filesystems are unsupported; preserve and migrate them manually."
  fi
  efi_dev=$(locate_efi_partition "$MNT/etc/fstab" "$target_dev" "${2:-}")
  [[ -b "$efi_dev" ]] || fail "Invalid EFI block device: $efi_dev"
  [[ $(blkid -s TYPE -o value "$efi_dev") == vfat &&
     $(lsblk -dnro PARTTYPE "$efi_dev") == c12a7328-f81f-11d2-ba4b-00a0c93ec93b ]] || fail "EFI device must be a FAT EFI System Partition."
  efi_uuid=$(blkid -s UUID -o value "$efi_dev")
  [[ -n "$efi_uuid" ]] || fail "EFI filesystem UUID is missing."
  if findmnt -rn --source "UUID=$efi_uuid" >/dev/null; then
    fail "EFI filesystem is already mounted. Unmount it before continuing."
  fi
  render_fstab "$MNT/etc/fstab" "$root_uuid" "$efi_uuid" > "$WORK_DIR/fstab"
  kernel_options=$(render_kernel_options "$MNT/etc/kernelstub/configuration")
  chroot "$MNT" /bin/bash -eu -c 'command -v kernelstub; command -v update-initramfs; command -v findmnt; command -v bootctl; command -v apt-get' >/dev/null
  local kernel required_space available_space
  local -a kernels
  shopt -s nullglob
  kernels=("$MNT"/boot/vmlinuz-*)
  shopt -u nullglob
  (( ${#kernels[@]} > 0 )) || fail "No installed kernel found."
  for kernel in "${kernels[@]}"; do
    [[ -s "$kernel" && -s "$MNT/boot/initrd.img-${kernel##*/vmlinuz-}" ]] || fail "Kernel/initramfs pair is missing or empty: $kernel"
  done
  # Conservatively reserve a full extra copy plus metadata headroom for cross-
  # subvolume moves. Reflinks may use less, but migration must tolerate copying.
  if (( ! repair_boot )); then
    required_space=$(du -sx -B1 "$MNT" | awk '{print $1}')
    available_space=$(df -B1 --output=avail "$MNT" | awk 'NR == 2 {print $1}')
    (( available_space >= required_space + 268435456 )) || fail "Insufficient free space for migration: reserve an extra installation-sized copy plus 256 MiB."
  fi
  mkdir "$WORK_DIR/efi"
  mount -t vfat "$efi_dev" "$WORK_DIR/efi"
  mount --make-private "$WORK_DIR/efi"
  umount "$WORK_DIR/efi"
  rmdir "$WORK_DIR/efi"

  if (( repair_boot )); then
    log_info "Repairing boot configuration on $target_dev; EFI device is $efi_dev..."
  else
    log_info "Converting installation on $target_dev; EFI device is $efi_dev..."
    mount -o remount,rw "$MNT"
    MIGRATION_STARTED=1
    for path in @ @home @snapshots; do
      btrfs subvolume create "$MNT/$path"
    done
    copy_directory_metadata "$MNT" "$MNT/@"
    migrate_layout "$MNT"
  fi
  umount "$MNT"

  mount -t btrfs -o subvol=@ "$target_dev" "$MNT"
  mount --make-private "$MNT"
  mkdir -p "$MNT/home" "$MNT/.snapshots" "$MNT/boot/efi"
  mount -t btrfs -o subvol=@home "$target_dev" "$MNT/home"
  mount -t btrfs -o subvol=@snapshots "$target_dev" "$MNT/.snapshots"
  mount -t vfat "$efi_dev" "$MNT/boot/efi"
  for path in /dev /proc /sys /run; do
    mkdir -p "$MNT$path"
    mount --rbind "$path" "$MNT$path"
    mount --make-rslave "$MNT$path"
  done

  local new_fstab backup
  new_fstab=$(mktemp "$MNT/etc/.fstab.XXXXXX")
  cp --preserve=all "$MNT/etc/fstab" "$new_fstab"
  cat "$WORK_DIR/fstab" > "$new_fstab"
  chroot "$MNT" findmnt --verify --tab-file "/etc/${new_fstab##*/}"
  backup=$(mktemp "$MNT/etc/fstab.pre-btrfs.XXXXXX")
  cp --preserve=all "$MNT/etc/fstab" "$backup"
  mv -T -- "$new_fstab" "$MNT/etc/fstab"
  log_info "Original fstab saved at ${backup#"$MNT"}."
  backup=$(mktemp "$MNT/etc/kernelstub/configuration.pre-btrfs.XXXXXX")
  cp --preserve=all "$MNT/etc/kernelstub/configuration" "$backup"
  local boot_file
  for boot_file in loader/loader.conf loader/entries/Pop_OS-current.conf; do
    if [[ -f "$MNT/boot/efi/$boot_file" ]]; then
      backup=$(mktemp "$MNT/etc/kernelstub/${boot_file##*/}.pre-btrfs.XXXXXX")
      cp -- "$MNT/boot/efi/$boot_file" "$backup"
    fi
  done

  log_info "Configuring boot options, ensuring Btrfs tools are installed, and rebuilding initramfs..."
  configure_boot "$MNT" "$kernel_options"
  verify_boot_entry "$MNT/boot/efi" "$root_uuid"
  verify_boot_selection "$MNT/boot/efi"
  verify_target_bootloader "$MNT"
  log_info "Unmounting target filesystems..."
  umount -R "$MNT"
  rmdir -- "$MNT"
  MNT=""
  rm -f -- "$WORK_DIR/fstab"
  rmdir -- "$WORK_DIR"
  WORK_DIR=""
  log_info "Success! Btrfs subvolumes @, @home and @snapshots and the current boot entry are configured."
  log_info "You may now reboot into your Pop!_OS installation."
}

# Sourcing exposes the pure helpers for tests, without invoking disk operations.
if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
  [[ $EUID -eq 0 ]] || fail "Run this script as root from the live USB (sudo)."
  command -v unshare >/dev/null || fail "Required command missing: unshare"
  # Isolate mount propagation before any bind mounts or recursive unmounts.
  # Sourcing in the child runs main once without re-entering this launcher.
  # shellcheck disable=SC2016 # Positional arguments belong to the child shell.
  exec unshare --mount --propagation private /bin/bash -c 'source "$1"; main "${@:2}"' \
    bash "$SCRIPT_DIR/${BASH_SOURCE[0]##*/}" "$@"
fi
