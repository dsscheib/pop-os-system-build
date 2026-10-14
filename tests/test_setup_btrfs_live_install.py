"""Regression checks using temporary files and command stubs; no disk operations.

Run with: python3 -m unittest discover -s tests -v
"""
import json
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "setup_btrfs_live_install.sh"


class LiveInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="btrfs-live-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def bash(self, command, *args):
        return subprocess.run(
            ["bash", "-c", f"source {shlex.quote(str(SCRIPT))}\n{command}",
             "test", *map(str, args)], text=True, capture_output=True, check=False
        )

    def fstab(self, text):
        path = self.root / "fstab"
        path.write_text(text)
        return self.bash('render_fstab "$1" root-uuid efi-uuid', path)

    def test_fstab_preserves_options_comments_and_other_filesystems(self):
        recovery = "UUID=recovery /recovery vfat defaults 0 2"
        result = self.fstab(
            "# UUID=old / btrfs defaults 0 0\n"
            "UUID=old / btrfs defaults,noatime,compress=zstd,subvolid=5 0 0 # root\n"
            "UUID=old /home btrfs nodev,subvol=/old 0 0\n"
            "UUID=old /.snapshots btrfs noatime,subvol=/old 0 0\n"
            "PARTUUID=old /boot/efi vfat umask=0077 0 1\n" + recovery + "\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], "# UUID=old / btrfs defaults 0 0")
        entries = {line.split()[1]: line.split() for line in lines if not line.startswith("#")}
        self.assertEqual(entries["/"][3:6], ["defaults,noatime,compress=zstd,subvol=@", "0", "0"])
        self.assertEqual(entries["/"][6:], ["#", "root"])
        self.assertEqual(entries["/home"][:4], ["UUID=root-uuid", "/home", "btrfs", "nodev,subvol=@home"])
        self.assertEqual(entries["/.snapshots"][3], "noatime,subvol=@snapshots")
        self.assertEqual(entries["/boot/efi"][0], "UUID=efi-uuid")
        self.assertIn(recovery, lines)

    def test_fstab_defaults_and_missing_mounts(self):
        result = self.fstab("UUID=old / btrfs defaults 0 0\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        entries = {line.split()[1]: line.split() for line in result.stdout.splitlines()}
        self.assertEqual(entries["/"][3:], ["defaults,subvol=@", "0", "0"])
        self.assertEqual(set(entries), {"/", "/home", "/.snapshots", "/boot/efi"})

    def test_fstab_without_defaults_or_numeric_fields(self):
        result = self.fstab("UUID=old / btrfs noatime,compress=zstd # note\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[0].split()[3:],
                         ["noatime,compress=zstd,subvol=@", "0", "0", "#", "note"])

    def test_fstab_rewrite_is_idempotent(self):
        first = self.fstab("UUID=old / btrfs defaults,subvol=/old 0 0\n")
        second = self.fstab(first.stdout)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(first.stdout, second.stdout)

    def test_invalid_fstab_is_rejected(self):
        for text in (
            "UUID=old / btrfs defaults 0 0\nUUID=old / btrfs defaults 0 0\n",
            "UUID=old /home btrfs defaults 0 0\n",
            "UUID=old / btrfs defaults nope 0\n",
            "malformed entry\n",
        ):
            with self.subTest(text=text):
                self.assertNotEqual(self.fstab(text).returncode, 0)

    def test_multiple_swap_entries_are_preserved(self):
        text = ("UUID=old / btrfs defaults 0 0\n"
                "UUID=swap none swap sw 0 0\n/swapfile none swap sw 0 0\n")
        result = self.fstab(text)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("/swapfile none swap sw 0 0", result.stdout)

    def test_kernel_options_preserve_flags_and_normalize_selector(self):
        path = self.root / "configuration"
        path.write_text(json.dumps({"user": {"kernel_options": [
            "quiet", "splash", "rootflags=subvolid=5,compress=zstd", "rootflags=subvol=/old,noatime"
        ]}}))
        result = self.bash('render_kernel_options "$1"', path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "quiet splash rootflags=compress=zstd,noatime,subvol=@")

    def test_invalid_kernel_config_is_rejected(self):
        path = self.root / "configuration"
        path.write_text('{"user":{"kernel_options":"quiet"}}')
        self.assertNotEqual(self.bash('render_kernel_options "$1"', path).returncode, 0)

    def migration_fixture(self):
        for name in ("@", "@home", "@snapshots", "etc", "home/user"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        (self.root / "etc/fstab").write_text("fstab")
        (self.root / ".hidden").write_text("hidden root data")
        (self.root / "home/user/.hidden").write_text("hidden home data")

    def test_migration_includes_dotfiles(self):
        self.migration_fixture()
        result = self.bash('migrate_layout "$1"', self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "@/.hidden").read_text(), "hidden root data")
        self.assertEqual((self.root / "@home/user/.hidden").read_text(), "hidden home data")
        self.assertFalse((self.root / "home").exists())

    def test_failed_home_move_keeps_source_data(self):
        self.migration_fixture()
        result = self.bash('''
mv() {
  if [[ $3 == home/user ]]; then return 42; fi
  command mv "$@"
}
migrate_layout "$1"
''', self.root)
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assertEqual((self.root / "home/user/.hidden").read_text(), "hidden home data")

    def test_failed_root_move_stops_before_home_migration(self):
        self.migration_fixture()
        result = self.bash('mv() { return 43; }; migrate_layout "$1"', self.root)
        self.assertEqual(result.returncode, 43, result.stderr)
        self.assertTrue((self.root / "etc/fstab").exists())
        self.assertTrue((self.root / "home/user/.hidden").exists())

    def test_efi_resolution_uses_tags_and_rejects_ambiguity(self):
        path = self.root / "fstab"
        for tag in ("UUID", "LABEL", "PARTUUID", "PARTLABEL"):
            path.write_text(f"{tag}=efi /boot/efi vfat defaults 0 1\n")
            result = self.bash('findfs() { printf "%s\\n" "$1"; }; locate_efi_partition "$1" /dev/root', path)
            self.assertEqual(result.stdout.strip(), f"{tag}=efi")
            self.assertEqual(result.returncode, 0, result.stderr)
        path.write_text("UUID=root / btrfs defaults 0 0\n")
        result = self.bash('''
lsblk() {
  if [[ $1 == -dnro ]]; then printf 'disk\n'; else
    printf '/dev/efi1 c12a7328-f81f-11d2-ba4b-00a0c93ec93b\n/dev/efi2 c12a7328-f81f-11d2-ba4b-00a0c93ec93b\n'
  fi
}
locate_efi_partition "$1" /dev/root
''', path)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No unique EFI", result.stderr)

    def test_efi_recovery_label_is_not_selected(self):
        path = self.root / "fstab"
        path.write_text("UUID=root / btrfs defaults 0 0\n")
        result = self.bash('''
lsblk() {
  if [[ $1 == -dnro ]]; then printf 'disk\n'; else
    printf '/dev/recovery ebd0a0a2-b9e5-4433-87c0-68b6b72699c7\n/dev/efi c12a7328-f81f-11d2-ba4b-00a0c93ec93b\n'
  fi
}
locate_efi_partition "$1" /dev/root
''', path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "/dev/efi")

    def boot_fixture(self, options):
        entry = self.root / "loader/entries/Pop_OS-current.conf"
        entry.parent.mkdir(parents=True, exist_ok=True)
        for name in ("vmlinuz.efi", "initrd.img"):
            (self.root / name).write_text("image data")
        entry.write_text(f"linux /vmlinuz.efi\ninitrd /initrd.img\noptions {options}\n")

    def test_boot_entry_verifies_root_flags_and_images(self):
        self.boot_fixture("root=UUID=root-uuid ro rootflags=compress=zstd,subvol=@")
        self.assertEqual(self.bash('verify_boot_entry "$1" root-uuid', self.root).returncode, 0)
        (self.root / "initrd.img").unlink()
        self.assertNotEqual(self.bash('verify_boot_entry "$1" root-uuid', self.root).returncode, 0)

    def test_conflicting_or_inexact_boot_options_are_rejected(self):
        for options in (
            "root=UUID=wrong rootflags=subvol=@",
            "root=UUID=root-uuid rootflags=subvol=@old",
            "root=UUID=root-uuid rootflags=subvol=@,subvolid=5",
            "root=UUID=root-uuid rootflags=subvol=@ rootflags=subvol=other",
        ):
            with self.subTest(options=options):
                self.boot_fixture(options)
                self.assertNotEqual(self.bash('verify_boot_entry "$1" root-uuid', self.root).returncode, 0)

    def test_cleanup_preserves_nonempty_directory(self):
        mount = self.root / "mount"
        mount.mkdir()
        (mount / "keep").write_text("data")
        result = self.bash('MNT=$1; mountpoint() { return 1; }; trap cleanup EXIT', mount)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((mount / "keep").read_text(), "data")

    def test_cleanup_unmount_failure_is_fatal(self):
        mount = self.root / "mount"
        mount.mkdir()
        (mount / "keep").write_text("data")
        result = self.bash('''
MNT=$1
mountpoint() { return 0; }
umount() { return 1; }
trap cleanup EXIT
''', mount)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unmount failed", result.stderr)
        self.assertEqual((mount / "keep").read_text(), "data")

    def test_cleanup_preserves_original_failure_status(self):
        mount = self.root / "mount"
        mount.mkdir()
        result = self.bash('MNT=$1; mountpoint() { return 1; }; trap cleanup EXIT; exit 42', mount)
        self.assertEqual(result.returncode, 42)
        self.assertFalse(mount.exists())

    def boot_commands(self, kernel_status, initramfs_status):
        commands = self.root / "bin"
        commands.mkdir()
        log = self.root / "commands.log"
        for name, status in (("kernelstub", kernel_status), ("update-initramfs", initramfs_status)):
            path = commands / name
            path.write_text(f'#!/usr/bin/env bash\nprintf "%s\\n" {name} >> "$BOOT_TEST_LOG"\nexit {status}\n')
            path.chmod(0o755)
        result = self.bash('''
export PATH="$2:$PATH" BOOT_TEST_LOG="$3"
chroot() { shift; "$@"; }
configure_boot "$1" "quiet rootflags=subvol=@"
''', self.root, commands, log)
        return result, log.read_text().splitlines()

    def test_kernelstub_failure_stops_boot_configuration(self):
        result, commands = self.boot_commands(42, 0)
        self.assertEqual(result.returncode, 42)
        self.assertEqual(commands, ["kernelstub"])

    def test_initramfs_failure_stops_boot_configuration(self):
        result, commands = self.boot_commands(0, 43)
        self.assertEqual(result.returncode, 43)
        self.assertEqual(commands, ["kernelstub", "update-initramfs"])

    def test_successful_boot_configuration_refreshes_esp_after_initramfs(self):
        result, commands = self.boot_commands(0, 0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(commands, ["kernelstub", "update-initramfs", "kernelstub"])

    def test_boot_selection_must_reference_validated_current_entry(self):
        loader = self.root / "loader"
        loader.mkdir()
        config = loader / "loader.conf"
        config.write_text("default Pop_OS-current\n")
        self.assertEqual(self.bash('verify_boot_selection "$1"', self.root).returncode, 0)
        config.write_text("default Pop_OS-oldkern\n")
        self.assertNotEqual(self.bash('verify_boot_selection "$1"', self.root).returncode, 0)

    def test_relative_os_release_symlink_is_supported(self):
        etc = self.root / "etc"
        vendor = etc / "pop-os"
        vendor.mkdir(parents=True)
        (vendor / "os-release").write_text('ID=pop\nVERSION_ID="24.04"\n')
        (etc / "os-release").symlink_to("pop-os/os-release")
        # Model chroot's file resolution without requiring mount/root privileges.
        result = self.bash('chroot() { command cat "$1/etc/os-release"; }; is_pop_installation "$1"', self.root)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_directory_permissions_and_xattrs_are_preserved(self):
        source = self.root / "source"
        destination = self.root / "destination"
        source.mkdir(mode=0o750)
        destination.mkdir()
        import os
        try:
            os.setxattr(source, "user.pop-test", b"metadata")
        except OSError as error:
            self.skipTest(f"Extended attributes are unavailable: {error}")
        result = self.bash('copy_directory_metadata "$1" "$2"', source, destination)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(destination.stat().st_mode & 0o777, 0o750)
        self.assertEqual(os.getxattr(destination, "user.pop-test"), b"metadata")


if __name__ == "__main__":
    unittest.main()
