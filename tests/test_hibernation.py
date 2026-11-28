"""Hibernation regressions: fixture files and fake commands, never real disk I/O."""
import contextlib
import importlib
import io
import json
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
hibernation = importlib.import_module('configure_hibernation')
GIB = hibernation.GIB


class Fixture(hibernation.Hibernation):
    def __init__(self, root):
        super().__init__(root)
        self.calls = []
        self.active = False
        self.offset = '123456'
        self.failure = None
        self.filesystem = 'btrfs'
        self.subvolume = '/@'
        self.devices = '1'
        self.version = 'v6.6.3'
        self.units = 'loaded'
        self.swap_type = 'swap'
        self.mount_uuid = 'aaaa-bbbb'
        self.mounted_swap = False
        self.fail_publish = False
        self.sleep_override = False
        self.suspend_then_hibernate_override = False
        # `lsinitrd` output of a Pop!_OS 26.04 dracut image (format from dracut 110).
        self.dracut_listing = '\n'.join(
            f'-rwxr-xr-x   1 root     root        27232 Sep 16 14:55 {name}'
            for name in hibernation.DRACUT_RESUME_FILES)

    def run(self, *args):
        self.calls.append(args)
        if self.failure and args[0] == self.failure:
            raise subprocess.CalledProcessError(42, args, stderr='fixture failure')
        if args[0] == 'findmnt':
            if '--verify' in args:
                return ''
            point = args[-1]
            field = args[3]
            if field == 'TARGET':
                return '/swap' if self.mounted_swap else '/'
            if field == 'FSTYPE':
                return 'vfat' if point == '/boot/efi' else self.filesystem
            if field == 'FSROOT':
                return '/@swap' if point == '/swap' else self.subvolume
            return self.mount_uuid
        if args[:2] == ('btrfs', 'version'):
            return 'btrfs-progs ' + self.version
        if args[:3] == ('btrfs', 'filesystem', 'show'):
            return f'Total devices {self.devices} FS bytes used 123'
        if args[:3] == ('btrfs', 'filesystem', 'mkswapfile'):
            file = Path(args[-1])
            file.parent.mkdir(parents=True, exist_ok=True)
            with file.open('wb') as stream:
                stream.truncate(self.size)  # Sparse fixture; never allocate RAM-sized data.
            return ''
        if args[:3] == ('btrfs', 'inspect-internal', 'map-swapfile'):
            return self.offset
        if args[0] == 'blkid':
            return self.swap_type
        if args[0] == 'swapon':
            if '--show=NAME' in args:
                return str(self.path(hibernation.SWAP)) if self.active else ''
            self.active = True
            return ''
        if args[0] == 'systemctl' and args[1] == 'show':
            return self.units
        if args[0] == 'systemd-analyze':
            text = self.read('/etc/systemd/sleep.conf.d/90-pop-hibernation.conf')
            return (text + ('AllowHibernation=no\n' if self.sleep_override else '')
                    + ('AllowSuspendThenHibernate=no\n' if self.suspend_then_hibernate_override else ''))
        if args[0] == 'lsinitramfs':
            return 'scripts/local-premount/resume\nconf/conf.d/resume'
        if args[0] == 'lsinitrd':
            return self.dracut_listing
        if args[0] == 'kernelstub' and not self.fail_publish:
            options = json.loads(self.read('/etc/kernelstub/configuration'))['user']['kernel_options']
            self.path('/boot/efi/loader/entries/Pop_OS-current.conf').write_text(
                'linux /EFI/Pop_OS-test/vmlinuz.efi\ninitrd /EFI/Pop_OS-test/initrd.img\n'
                'options root=UUID=aaaa-bbbb ' + ' '.join(options) + '\n')
        return ''

    def ensure_subvolume(self):
        self.calls.append(('ensure-subvolume',))
        swap_dir = self.path('/swap')
        existed = swap_dir.exists()
        swap_dir.mkdir(exist_ok=True)
        return not existed


class HibernationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.setup = Fixture(self.root)
        files = {
            '/etc/os-release': 'ID=pop\n',
            '/sys/power/state': 'freeze mem disk\n',
            '/sys/power/disk': '[platform] shutdown reboot\n',
            '/sys/kernel/security/lockdown': '[none] integrity confidentiality\n',
            '/proc/meminfo': 'MemTotal:       16777216 kB\n',
            '/etc/fstab': '# keep\nUUID=aaaa-bbbb / btrfs subvol=@ 0 0\n'
                          '/oldswap none swap sw 0 0\n',
            '/etc/kernelstub/configuration': json.dumps({
                'default': {'kernel_options': ['quiet', 'resume=UUID=old']},
                'user': {'kernel_options': ['quiet', 'rootflags=subvol=@',
                                           'noresume', 'resume=UUID=old', 'resume_offset=7']}}),
            '/boot/efi/loader/entries/Pop_OS-current.conf': 'options quiet\n',
            '/boot/efi/EFI/Pop_OS-test/vmlinuz.efi': 'fixture kernel',
            '/boot/efi/EFI/Pop_OS-test/initrd.img': 'fixture initramfs',
            '/usr/share/initramfs-tools/scripts/local-premount/resume': 'resume_offset',
        }
        for name, content in files.items():
            path = self.setup.path(name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        self.commands = patch.object(hibernation.shutil, 'which', return_value='/fixture/tool')
        self.commands.start()
        self.addCleanup(self.commands.stop)
        self.space = patch.object(hibernation.shutil, 'disk_usage',
                                  return_value=type('Space', (), {'free': 100 * GIB})())
        self.space.start()
        self.addCleanup(self.space.stop)

    def test_size_includes_ram_and_headroom_rounded_up(self):
        self.assertEqual(hibernation.swap_size('MemTotal: 16777216 kB\n'), 20 * GIB)
        self.assertEqual(hibernation.swap_size('MemTotal: 1048576 kB\n'), 3 * GIB)
        self.assertEqual(hibernation.swap_size('MemTotal: 16777217 kB\n'), 21 * GIB)
        for text in ('', 'MemTotal: 0 kB\n'):
            with self.assertRaises(ValueError):
                hibernation.swap_size(text)

    def test_preflight_does_not_change_configuration(self):
        before = self.setup.read('/etc/fstab')
        self.setup.preflight()
        self.assertEqual(self.setup.read('/etc/fstab'), before)
        self.assertFalse(self.setup.path('/swap').exists())
        self.assertFalse(any(call[0] in ('mount', 'swapon', 'kernelstub', 'update-initramfs')
                             for call in self.setup.calls))

    def test_full_configuration_preserves_existing_swap_and_is_repeatable(self):
        lid = self.setup.path('/etc/systemd/logind.conf.d/90-lid-close.conf')
        lid.parent.mkdir(parents=True)
        lid.write_text('[Login]\nHandleLidSwitch=suspend\n')
        self.assertTrue(self.setup.configure())
        swap = self.setup.path(hibernation.SWAP)
        self.assertEqual(swap.stat().st_size, 20 * GIB)
        self.assertEqual(swap.stat().st_mode & 0o777, 0o600)
        fstab = self.setup.read('/etc/fstab')
        self.assertIn('/oldswap none swap sw 0 0', fstab)
        self.assertIn('subvol=@swap', fstab)
        self.assertIn('RESUME=UUID=aaaa-bbbb', self.setup.read('/etc/initramfs-tools/conf.d/resume'))
        sleep_conf = self.setup.read('/etc/systemd/sleep.conf.d/90-pop-hibernation.conf')
        self.assertIn('AllowHibernation=yes', sleep_conf)
        self.assertIn('AllowSuspendThenHibernate=yes', sleep_conf)
        self.assertIn('HibernateDelaySec=1h', sleep_conf)
        self.assertEqual(lid.read_text(), '[Login]\nHandleLidSwitch=suspend-then-hibernate\n'
                         'HandleLidSwitchExternalPower=suspend\nHandleLidSwitchDocked=ignore\n')
        lid_backup = lid.with_name(lid.name + '.pre-hibernation')
        self.assertEqual(lid_backup.read_text(), '[Login]\nHandleLidSwitch=suspend\n')
        # Ubuntu's polkit rule disables hibernation; ours must be installed and sort first.
        rule = self.setup.path(hibernation.POLKIT_RULE)
        self.assertEqual(rule.read_text(), hibernation.POLKIT_RULE_TEXT)
        self.assertEqual(rule.stat().st_mode & 0o777, 0o644)
        self.assertLess(rule.name, 'com.ubuntu.desktop.rules')
        self.assertIn('"org.freedesktop.login1.hibernate"', rule.read_text())
        self.assertIn('subject.local && subject.active', rule.read_text())
        hook = self.setup.path(hibernation.S2H_HOOK)
        self.assertEqual(hook.read_text(), hibernation.S2H_HOOK_TEXT)
        self.assertEqual(hook.stat().st_mode & 0o777, 0o755)
        options = json.loads(self.setup.read('/etc/kernelstub/configuration'))['user']['kernel_options']
        self.assertEqual(options, ['quiet', 'rootflags=subvol=@', 'resume=UUID=aaaa-bbbb', 'resume_offset=123456'])
        commands = [call[0] for call in self.setup.calls]
        self.assertIn('update-initramfs', commands)
        self.assertIn('kernelstub', commands)
        self.assertIn(('systemctl', 'daemon-reload'), self.setup.calls)
        self.assertLess(commands.index('update-initramfs'), commands.index('kernelstub'))
        self.assertNotIn('swapoff', commands)
        original_backup = self.setup.read('/etc/fstab.pre-hibernation')
        self.setup.calls.clear()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            second_changed = self.setup.configure()
        self.assertFalse(second_changed)
        self.assertIn('Hibernation already configured:', buf.getvalue())
        self.assertEqual(self.setup.read('/etc/fstab'), fstab)
        self.assertEqual(self.setup.read('/etc/fstab.pre-hibernation'), original_backup)
        self.assertEqual(lid_backup.read_text(), '[Login]\nHandleLidSwitch=suspend\n')
        second_commands = [call[0] for call in self.setup.calls]
        self.assertNotIn('update-initramfs', second_commands)
        self.assertNotIn('kernelstub', second_commands)
        self.assertNotIn(('systemctl', 'daemon-reload'), self.setup.calls)
        self.assertEqual(sum(call[:3] == ('btrfs', 'filesystem', 'mkswapfile')
                             for call in self.setup.calls), 0)
        self.assertEqual(sum(call[0] == 'swapon' and '--priority' in call
                             for call in self.setup.calls), 0)

    def test_replacing_an_older_sleep_hook_leaves_no_second_hook(self):
        # systemd-sleep runs every executable in the hook directory, so a backup
        # copy of the old hook would run as well.
        hook = self.setup.path(hibernation.S2H_HOOK)
        hook.parent.mkdir(parents=True)
        hook.write_text('#!/bin/sh\nexit 0\n')
        hook.chmod(0o755)
        self.assertTrue(self.setup.configure())
        self.assertEqual(hook.read_text(), hibernation.S2H_HOOK_TEXT)
        self.assertEqual(sorted(path.name for path in hook.parent.iterdir()), [hook.name])

    def test_refuses_symlinked_sleep_hook(self):
        hook = self.setup.path(hibernation.S2H_HOOK)
        hook.parent.mkdir(parents=True)
        hook.symlink_to(self.root / 'elsewhere')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.setup.preflight()
        self.assertFalse((self.root / 'elsewhere').exists())

    def use_dracut(self):
        """Turn the fixture into Pop!_OS 26.04, where dracut replaces initramfs-tools."""
        self.setup.path(hibernation.INITRAMFS_TOOLS_RESUME).unlink()
        module = self.setup.path(hibernation.DRACUT_MODULES + '/74resume/module-setup.sh')
        module.parent.mkdir(parents=True)
        module.write_text('# dracut resume module\n')

    def test_dracut_configuration_includes_resume_module_and_is_repeatable(self):
        self.use_dracut()
        self.assertTrue(self.setup.configure())
        self.assertEqual(self.setup.read(hibernation.DRACUT_CONF), 'add_dracutmodules+=" resume "\n')
        self.assertFalse(self.setup.path(hibernation.INITRAMFS_TOOLS_CONF).exists())
        options = json.loads(self.setup.read('/etc/kernelstub/configuration'))['user']['kernel_options']
        self.assertIn('resume=UUID=aaaa-bbbb', options)
        self.assertIn('resume_offset=123456', options)
        commands = [call[0] for call in self.setup.calls]
        self.assertLess(commands.index('update-initramfs'), commands.index('kernelstub'))
        self.assertIn(('lsinitrd', str(self.setup.path('/boot/efi/EFI/Pop_OS-test/initrd.img'))),
                      self.setup.calls)
        self.assertNotIn('lsinitramfs', commands)
        self.assertTrue(self.setup.path(hibernation.POLKIT_RULE).exists())
        self.setup.calls.clear()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(self.setup.configure())
        self.assertNotIn('update-initramfs', [call[0] for call in self.setup.calls])

    def test_dracut_image_without_systemd_resume_stops_before_lid_policy(self):
        self.use_dracut()
        for missing in hibernation.DRACUT_RESUME_FILES:
            with self.subTest(missing=missing):
                self.setup.dracut_listing = '\n'.join(
                    f'-rw-r--r--   1 root root 666 Sep 16 14:55 {name}'
                    for name in hibernation.DRACUT_RESUME_FILES if name != missing)
                with self.assertRaisesRegex(ValueError, 'systemd-hibernate-resume'):
                    self.setup.configure()
                self.assertFalse(self.setup.path('/etc/systemd/logind.conf.d/90-lid-close.conf').exists())
                self.assertFalse(self.setup.path(hibernation.POLKIT_RULE).exists())

    def test_requires_an_initramfs_generator_with_resume_support(self):
        self.setup.path(hibernation.INITRAMFS_TOOLS_RESUME).unlink()
        with self.assertRaisesRegex(ValueError, 'initramfs-tools, or dracut'):
            self.setup.preflight()
        self.setup.path(hibernation.DRACUT_MODULES + '/74resume').mkdir(parents=True)
        with patch.object(hibernation.shutil, 'which',
                          side_effect=lambda name: None if name == 'lsinitrd' else '/fixture/tool'):
            with self.assertRaisesRegex(ValueError, 'Required command missing: lsinitrd'):
                self.setup.preflight()
        self.assertFalse(self.setup.path('/swap').exists())

    def test_drifted_efi_entry_triggers_boot_refresh(self):
        self.assertTrue(self.setup.configure())
        self.setup.calls.clear()
        entry = self.setup.path('/boot/efi/loader/entries/Pop_OS-current.conf')
        entry.write_text('linux /EFI/Pop_OS-test/vmlinuz.efi\n'
                         'initrd /EFI/Pop_OS-test/initrd.img\n'
                         'options root=UUID=aaaa-bbbb quiet resume=UUID=aaaa-bbbb\n')
        self.assertTrue(self.setup.configure())
        commands = [call[0] for call in self.setup.calls]
        self.assertIn('update-initramfs', commands)
        self.assertIn('kernelstub', commands)
        self.assertLess(commands.index('update-initramfs'), commands.index('kernelstub'))

    @unittest.skipUnless(shutil.which('swapon') and Path('/proc/swaps').exists(),
                         'Requires util-linux swapon and /proc/swaps')
    def test_real_swapon_query_returns_only_active_filenames(self):
        # Read-only integration check: a stub that returns only NAME regardless
        # of arguments previously hid util-linux appending its default columns.
        setup = hibernation.Hibernation()
        reported = setup.run('swapon', '--show=NAME', '--noheadings', '--raw').splitlines()
        expected = [line.split()[0] for line in Path('/proc/swaps').read_text().splitlines()[1:]]
        self.assertEqual(sorted(reported), sorted(expected))

    def test_rejects_unsupported_systems_before_disk_mutation(self):
        cases = [('/sys/power/state', 'freeze mem'),
                 ('/sys/kernel/security/lockdown', 'none [integrity] confidentiality'),
                 ('/etc/os-release', 'ID=ubuntu'),
                 ('/sys/power/disk', 'reboot test_resume')]
        for name, value in cases:
            with self.subTest(name=name):
                original = self.setup.read(name)
                self.setup.path(name).write_text(value)
                with self.assertRaises(ValueError):
                    self.setup.preflight()
                self.assertFalse(self.setup.path('/swap').exists())
                self.setup.path(name).write_text(original)
        for field, value in [('version', 'v5.16.2'), ('devices', '2'),
                             ('filesystem', 'ext4'), ('subvolume', '/'), ('units', 'masked')]:
            with self.subTest(field=field):
                original = getattr(self.setup, field)
                setattr(self.setup, field, value)
                with self.assertRaises(ValueError):
                    self.setup.preflight()
                setattr(self.setup, field, original)

    def test_rejects_insufficient_space(self):
        with patch.object(hibernation.shutil, 'disk_usage',
                          return_value=type('Space', (), {'free': 21 * GIB})()):
            with self.assertRaisesRegex(ValueError, 'free'):
                self.setup.preflight()

    def test_rejects_foreign_mount_and_preserves_fstab(self):
        original = 'UUID=other /swap btrfs subvol=@home 0 0\n'
        self.setup.path('/etc/fstab').write_text(original)
        with self.assertRaisesRegex(ValueError, 'another mount'):
            self.setup.preflight()
        self.assertEqual(self.setup.read('/etc/fstab'), original)

    def test_existing_small_swap_is_never_resized(self):
        path = self.setup.path(hibernation.SWAP)
        path.parent.mkdir()
        path.write_text('existing swap data')
        with self.assertRaisesRegex(ValueError, 'too small'):
            self.setup.configure()
        self.assertEqual(path.read_text(), 'existing swap data')

    def test_mapping_or_activation_failure_does_not_publish_resume(self):
        for failure in ('invalid-offset', 'swapon'):
            with self.subTest(failure=failure):
                self.setup.offset = 'not-an-offset' if failure == 'invalid-offset' else '123456'
                self.setup.failure = 'swapon' if failure == 'swapon' else None
                with self.assertRaises((ValueError, subprocess.CalledProcessError)):
                    self.setup.configure()
                self.assertFalse(self.setup.path('/etc/initramfs-tools/conf.d/resume').exists())
                self.assertFalse(any(call[0] == 'kernelstub' for call in self.setup.calls))

    def test_initramfs_failure_never_refreshes_efi(self):
        self.setup.failure = 'update-initramfs'
        with self.assertRaises(subprocess.CalledProcessError):
            self.setup.configure()
        self.assertFalse(any(call[0] == 'kernelstub' for call in self.setup.calls))
        self.assertFalse(self.setup.path('/etc/systemd/logind.conf.d/90-lid-close.conf').exists())
        self.assertFalse(self.setup.path(hibernation.POLKIT_RULE).exists())

    def test_verifies_published_boot_parameters(self):
        self.setup.fail_publish = True
        with self.assertRaisesRegex(ValueError, 'EFI boot entry'):
            self.setup.configure()

    def test_reports_conflicting_sleep_configuration(self):
        self.setup.sleep_override = True
        with self.assertRaisesRegex(ValueError, 'overrides AllowHibernation'):
            self.setup.configure()

    def test_reports_suspend_then_hibernate_disabled_elsewhere(self):
        # The lid uses suspend-then-hibernate, so a drop-in disabling it must stop the run.
        self.setup.suspend_then_hibernate_override = True
        with self.assertRaisesRegex(ValueError, 'overrides AllowSuspendThenHibernate'):
            self.setup.configure()
        self.assertFalse(self.setup.path('/etc/systemd/logind.conf.d/90-lid-close.conf').exists())

    def test_failed_unmount_does_not_remove_mountpoint_contents(self):
        directory = self.root / 'top-level'
        directory.mkdir()
        important = directory / 'keep'
        important.write_text('filesystem data')
        self.setup.uuid = 'aaaa-bbbb'
        self.setup.failure = 'umount'
        with patch.object(hibernation.tempfile, 'mkdtemp', return_value=str(directory)):
            with self.assertRaises(subprocess.CalledProcessError):
                with self.setup.top_level():
                    pass
        self.assertEqual(important.read_text(), 'filesystem data')

    def test_mounted_swap_must_be_on_expected_filesystem(self):
        self.setup.path('/swap').mkdir()
        self.setup.mounted_swap = True
        self.setup.uuid = 'aaaa-bbbb'
        self.setup.mount_uuid = 'cccc-dddd'
        with self.assertRaisesRegex(ValueError, 'another subvolume'):
            hibernation.Hibernation.ensure_subvolume(self.setup)

    def test_unmounted_swap_directory_must_be_empty(self):
        self.setup.path('/swap').mkdir()
        self.setup.path('/swap/keep').touch()
        with self.assertRaisesRegex(ValueError, 'must be empty'):
            hibernation.Hibernation.ensure_subvolume(self.setup)

    def test_creates_and_mounts_swap_from_top_level(self):
        self.setup.uuid = 'aaaa-bbbb'
        directory = self.root / 'top-level'
        directory.mkdir()
        with patch.object(hibernation.tempfile, 'mkdtemp', return_value=str(directory)):
            hibernation.Hibernation.ensure_subvolume(self.setup)
        commands = self.setup.calls
        self.assertTrue(any(call[:3] == ('btrfs', 'subvolume', 'create')
                            and call[-1].endswith('/@swap') for call in commands))
        mounts = [call for call in commands if call[0] == 'mount']
        self.assertIn('subvolid=5', mounts[0])
        self.assertIn('subvol=@swap,noatime', mounts[1])
        self.assertTrue(any(call[0] == 'umount' for call in commands))

    def test_fstab_update_is_idempotent(self):
        text = hibernation.swap_fstab(self.setup.read('/etc/fstab'), 'aaaa-bbbb')
        self.assertEqual(hibernation.swap_fstab(text, 'aaaa-bbbb'), text)

    def test_kernel_options_preserve_defaults_and_remove_conflicts(self):
        config = {'default': {'kernel_options': ['quiet rootflags=subvol=@ noresume',
                                                'resume=old', 'resume_offset=1']}}
        self.assertEqual(hibernation.resume_options(config, 'aaaa', '42'),
                         ['quiet', 'rootflags=subvol=@', 'resume=UUID=aaaa', 'resume_offset=42'])

    def test_kernel_options_preserve_quoted_values(self):
        config = {'user': {'kernel_options': ['quiet', 'acpi_osi="Windows 2022"']}}
        self.assertEqual(hibernation.resume_options(config, 'aaaa', '42'),
                         ['quiet', 'acpi_osi="Windows 2022"', 'resume=UUID=aaaa', 'resume_offset=42'])


INVOCATION = '0123456789abcdef0123456789abcdef'
SLEEP_CONF = """# /etc/systemd/sleep.conf
[Sleep]
#HibernateDelaySec=

# /etc/systemd/sleep.conf.d/90-pop-hibernation.conf
[Sleep]
AllowHibernation=yes
HibernateDelaySec=1h
"""


class SuspendThenHibernateHookTests(unittest.TestCase):
    """The sleep hook, run in-process against a fixture state file and a fake clock."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.state = Path(temp.name) / 'deadline'
        self.hook = {'__name__': 'pop_workstation_s2h_timer'}
        exec(compile(hibernation.S2H_HOOK_TEXT, hibernation.S2H_HOOK, 'exec'), self.hook)
        self.hook['STATE'] = str(self.state)
        self.now = 1000.0
        self.slept = []
        self.config = SLEEP_CONF

    def fake_run(self, args, **kwargs):
        self.assertEqual(args, ['systemd-analyze', 'cat-config', 'systemd/sleep.conf'])
        return subprocess.CompletedProcess(args, 0, stdout=self.config)

    def call(self, phase, operation='suspend-then-hibernate', action='suspend', invocation=INVOCATION):
        env = {'SYSTEMD_SLEEP_ACTION': action}
        if invocation is not None:
            env['INVOCATION_ID'] = invocation
        with contextlib.redirect_stderr(io.StringIO()) as log:
            result = self.hook['main'](['hook', phase, operation], env, clock=lambda: self.now,
                                       sleep=self.slept.append, run=self.fake_run)
        self.assertEqual(result, 0)
        return log.getvalue()

    def test_parses_systemd_time_spans(self):
        parse = self.hook['parse_timespan']
        for text, seconds in [('1h', 3600), ('2min', 120), ('1h 30min', 5400), ('1h30min', 5400),
                              ('90', 90), ('1.5h', 5400), ('45 s', 45), ('1d', 86400)]:
            with self.subTest(text=text):
                self.assertEqual(parse(text), seconds)
        for text in ('', 'infinity', '5M', '0', '1x', '-1h', 'h'):
            with self.subTest(text=text):
                self.assertIsNone(parse(text))

    def test_reads_the_effective_delay(self):
        self.assertEqual(self.hook['hibernate_delay'](self.fake_run), 3600)
        # A later drop-in wins; an empty assignment resets to unset.
        self.config = SLEEP_CONF + '\n# /etc/systemd/sleep.conf.d/99-test.conf\n[Sleep]\nHibernateDelaySec=2min\n'
        self.assertEqual(self.hook['hibernate_delay'](self.fake_run), 120)
        self.config = SLEEP_CONF + '[Sleep]\nHibernateDelaySec=\n'
        self.assertIsNone(self.hook['hibernate_delay'](self.fake_run))
        self.config = '[Other]\nHibernateDelaySec=1h\n'
        self.assertIsNone(self.hook['hibernate_delay'](self.fake_run))

    def test_early_timer_wake_waits_until_past_the_deadline(self):
        self.call('pre')
        deadline = 1000.0 + 3600
        self.now = deadline - 1.7  # The early wake reported in systemd#38193.
        log = self.call('post')
        self.assertEqual(len(self.slept), 1)
        self.assertAlmostEqual(self.now + self.slept[0], deadline + self.hook['MARGIN'])
        self.assertIn('systemd#38193', log)

    def test_wake_just_after_the_deadline_still_waits_out_the_margin(self):
        self.call('pre')
        self.now = 1000.0 + 3600 + 0.4
        self.call('post')
        self.assertAlmostEqual(self.slept[0], self.hook['MARGIN'] - 0.4)

    def test_manual_wake_returns_at_once(self):
        self.call('pre')
        for when in (1000.0 + 60, 1000.0 + 3600 - self.hook['EARLY'] - 0.01, 1000.0 + 3600 + 5):
            with self.subTest(when=when):
                self.now = when
                self.assertIn('not waiting', self.call('post'))
        self.assertEqual(self.slept, [])

    def test_battery_estimation_resuspend_keeps_the_first_deadline(self):
        self.call('pre')
        self.now = 2500.0
        self.call('post')
        self.call('pre')  # systemd suspends again within the same run.
        self.now = 1000.0 + 3600 - 1.0
        self.call('post')
        self.assertAlmostEqual(self.now + self.slept[0], 1000.0 + 3600 + self.hook['MARGIN'])

    def test_a_new_run_replaces_the_previous_deadline(self):
        self.call('pre')
        self.now = 9000.0
        self.call('pre', invocation='f' * 32)
        self.now = 9000.0 + 3600 - 1.0
        self.call('post', invocation='f' * 32)
        self.assertEqual(len(self.slept), 1)
        self.now = 1000.0 + 3600 - 1.0
        self.call('post')  # The old run's deadline is gone.
        self.assertEqual(len(self.slept), 1)

    def test_ignores_everything_but_the_suspend_step_of_suspend_then_hibernate(self):
        cases = [{'operation': 'suspend'}, {'operation': 'hibernate', 'action': 'hibernate'},
                 {'action': 'hibernate'}, {'action': 'suspend-after-failed-hibernate'},
                 {'invocation': None}, {'invocation': '../../etc/passwd'}]
        for case in cases:
            with self.subTest(case=case):
                self.call('pre', **case)
                self.assertFalse(self.state.exists())

    def test_unset_delay_watches_nothing(self):
        self.config = '[Sleep]\nAllowHibernation=yes\n'
        self.assertIn('not watching', self.call('pre'))
        self.assertFalse(self.state.exists())
        self.now = 1000.0 + 3600 - 1.0
        self.call('post')
        self.assertEqual(self.slept, [])

    def test_installed_script_never_fails_a_sleep(self):
        """Run the hook as systemd would, with systemd-analyze missing from PATH."""
        self.assertTrue(hibernation.S2H_HOOK_TEXT.startswith('#!/usr/bin/python3 -I\n'))
        script = self.state.with_name('hook')
        script.write_text(hibernation.S2H_HOOK_TEXT.replace(
            "STATE = '/run/pop-workstation-s2h-deadline'", f'STATE = {str(self.state)!r}'))
        self.assertIn(str(self.state), script.read_text())
        result = subprocess.run([sys.executable, '-I', str(script), 'pre', 'suspend-then-hibernate'],
                                env={'PATH': '', 'SYSTEMD_SLEEP_ACTION': 'suspend', 'INVOCATION_ID': INVOCATION},
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0)
        self.assertIn('pop-workstation-s2h-timer: skipped:', result.stderr)
        self.assertFalse(self.state.exists())
