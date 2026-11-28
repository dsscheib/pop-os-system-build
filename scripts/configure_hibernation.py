#!/usr/bin/env python3
"""Configure hibernation on the installed Pop!_OS Btrfs/systemd-boot layout.

Supports both initramfs generators: initramfs-tools (Pop!_OS 24.04) and dracut
(Pop!_OS 26.04, which replaces initramfs-tools).

Never hibernates the machine. Swap allocation is confined to @swap and its
hibernate.swap file; existing swap areas are preserved and never deactivated.
"""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile

from st_assets import atomic_text

GIB = 1024 ** 3
SWAP = '/swap/hibernate.swap'
# How long the lid-close suspend lasts before the machine hibernates.
HIBERNATE_DELAY = '1h'
INITRAMFS_TOOLS_RESUME = '/usr/share/initramfs-tools/scripts/local-premount/resume'
INITRAMFS_TOOLS_CONF = '/etc/initramfs-tools/conf.d/resume'
DRACUT_MODULES = '/usr/lib/dracut/modules.d'
# dracut leaves its resume module out of host-only images unless resume= was
# already on the running kernel's command line; always include it.
DRACUT_CONF = '/etc/dracut.conf.d/90-pop-hibernation.conf'
DRACUT_CONF_TEXT = 'add_dracutmodules+=" resume "\n'
# With dracut's systemd module, these resume from the resume= and
# resume_offset= kernel parameters that kernelstub publishes.
DRACUT_RESUME_FILES = ('usr/lib/systemd/system-generators/systemd-hibernate-resume-generator',
                       'usr/lib/systemd/system/systemd-hibernate-resume.service',
                       'usr/lib/systemd/systemd-hibernate-resume')
# Ubuntu's /usr/share/polkit-1/rules.d/com.ubuntu.desktop.rules answers NO to
# every hibernate action, so logind reports CanHibernate=no and users can't
# hibernate. Rules files are evaluated in lexical order across rules.d
# directories and the first answer wins; "50-…" sorts before "com.ubuntu…".
POLKIT_RULE = '/etc/polkit-1/rules.d/50-pop-workstation-hibernate.rules'
POLKIT_RULE_TEXT = '''\
// Installed by pop-os-system-build (scripts/configure_hibernation.py).
// Overrides Ubuntu's com.ubuntu.desktop.rules, which disables hibernation:
// allow it for the active local session, the same terms Ubuntu uses for suspend.
polkit.addRule(function(action, subject) {
    if ((action.id == "org.freedesktop.login1.hibernate" ||
         action.id == "org.freedesktop.login1.handle-hibernate-key" ||
         action.id == "org.freedesktop.upower.hibernate") &&
        subject.local && subject.active) {
        return polkit.Result.YES;
    }
    if (action.id == "org.freedesktop.login1.hibernate-multiple-sessions" &&
        subject.local && subject.active) {
        return polkit.Result.AUTH_ADMIN_KEEP;
    }
});
'''
# systemd 255 (24.04) reads sleep hooks only from /usr/lib/systemd/system-sleep.
# This file is ours alone; systemd-sleep would also run any executable backup
# copy left beside it, so it is written without one.
S2H_HOOK = '/usr/lib/systemd/system-sleep/pop-workstation-s2h-timer'
S2H_HOOK_TEXT = r'''#!/usr/bin/python3 -I
# Installed by pop-os-system-build (scripts/configure_hibernation.py).
#
# Works around systemd#38193: after the suspend step of suspend-then-hibernate,
# systemd checks its HibernateDelaySec wake timer once, without waiting. When the
# RTC wakes the machine slightly before that timer counts as expired, systemd
# takes it for a manual wake and stays awake instead of hibernating.
#
# systemd runs this hook, and waits for it, before checking the timer. On "pre"
# it records when the suspend-then-hibernate run went to sleep; on "post" it
# waits until just past the deadline if the wake came close to it, so systemd
# sees its timer fire. Other wakes return at once. It never fails a sleep.
import os
import re
import subprocess
import sys
import tempfile
import time

STATE = '/run/pop-workstation-s2h-deadline'
# A wake this close before the deadline is treated as the timer's. Reported
# early wakes are under 2 seconds; a manual wake inside the window hibernates.
EARLY = 10.0
# Wait until this long past the deadline.
MARGIN = 1.0
UNITS = {
    'us': 1e-6, 'usec': 1e-6, 'ms': 1e-3, 'msec': 1e-3,
    '': 1, 's': 1, 'sec': 1, 'second': 1, 'seconds': 1,
    'm': 60, 'min': 60, 'minute': 60, 'minutes': 60,
    'h': 3600, 'hr': 3600, 'hour': 3600, 'hours': 3600,
    'd': 86400, 'day': 86400, 'days': 86400,
    'w': 604800, 'week': 604800, 'weeks': 604800,
}


def log(message):
    print(f'pop-workstation-s2h-timer: {message}', file=sys.stderr, flush=True)


def parse_timespan(text):
    """Seconds in a systemd time span such as "1h", "90min" or "1h 30min"; None if unparseable."""
    text = text.strip()
    if not re.fullmatch(r'(?:\d+(?:\.\d+)?\s*[a-z]*\s*)+', text):
        return None
    total = 0.0
    for number, unit in re.findall(r'(\d+(?:\.\d+)?)\s*([a-z]*)', text):
        if unit not in UNITS:
            return None
        total += float(number) * UNITS[unit]
    return total if total > 0 else None


def hibernate_delay(run=subprocess.run):
    """The effective HibernateDelaySec= in seconds, or None when unset."""
    text = run(['systemd-analyze', 'cat-config', 'systemd/sleep.conf'],
               check=True, text=True, capture_output=True, timeout=10).stdout
    section, value = '', None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('[') and line.endswith(']'):
            section = line
        elif section == '[Sleep]' and line.startswith('HibernateDelaySec='):
            value = line.split('=', 1)[1]
    return parse_timespan(value) if value else None


def read_state():
    try:
        with open(STATE) as stream:
            invocation, deadline = stream.read().split()
        return invocation, float(deadline)
    except (OSError, ValueError):
        return None


def write_state(invocation, deadline):
    directory = os.path.dirname(STATE)
    fd, temporary = tempfile.mkstemp(dir=directory, prefix='.pop-workstation-s2h.')
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(f'{invocation} {deadline:.6f}\n')
        os.replace(temporary, STATE)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main(argv, env, clock=lambda: time.clock_gettime(time.CLOCK_BOOTTIME),
         sleep=time.sleep, run=subprocess.run):
    # Only the suspend step of suspend-then-hibernate; systemd sets INVOCATION_ID
    # per service run, which tells repeated suspends of one run from a new run.
    if (len(argv) != 3 or argv[2] != 'suspend-then-hibernate'
            or env.get('SYSTEMD_SLEEP_ACTION') != 'suspend'):
        return 0
    invocation = env.get('INVOCATION_ID', '')
    if not re.fullmatch(r'[0-9a-f]{32}', invocation):
        return 0
    state = read_state()
    if argv[1] == 'pre':
        # systemd sets its deadline once per run, just before the first
        # suspend; keep that first record when it re-suspends to measure battery.
        if state and state[0] == invocation:
            return 0
        delay = hibernate_delay(run)
        if delay is None:
            log('HibernateDelaySec= is unset or unreadable; not watching this run')
            return 0
        write_state(invocation, clock() + delay)
    elif argv[1] == 'post' and state and state[0] == invocation:
        remaining = state[1] - clock()
        if -MARGIN < remaining <= EARLY:
            log(f'{remaining:.2f} s to the hibernate deadline at wake; '
                f'waiting {remaining + MARGIN:.2f} s so systemd sees its timer (systemd#38193)')
            sleep(remaining + MARGIN)
        else:
            log(f'{remaining:.0f} s to the hibernate deadline at wake; not waiting')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv, os.environ))
    except Exception as error:  # Never block or fail a sleep.
        log(f'skipped: {error}')
        sys.exit(0)
'''


def swap_size(meminfo):
    match = re.search(r'^MemTotal:\s+(\d+)\s+kB$', meminfo, re.MULTILINE)
    if not match or int(match[1]) == 0:
        raise ValueError('Cannot determine installed RAM from /proc/meminfo')
    ram = int(match[1]) * 1024
    # Room for a RAM-sized image plus normal swap use and image metadata.
    return ((ram + max(2 * GIB, (ram + 3) // 4) + GIB - 1) // GIB) * GIB


def resume_options(config, uuid, offset):
    effective = dict(config.get('default', {}))
    effective.update(config.get('user', {}))
    options = effective.get('kernel_options')
    if not isinstance(options, list) or not all(isinstance(x, str) for x in options):
        raise ValueError('Invalid kernelstub kernel_options')
    # Some configurations store several parameters in one list element.
    text = ' '.join(options)
    shlex.split(text)  # Reject malformed quoting, but retain quotes in output.
    words = re.findall(r'''(?:[^\s"'\\]|\\.|"(?:[^"\\]|\\.)*"|'[^']*')+''', text)
    kept = [x for x in words if shlex.split(x)[0] not in ('noresume', 'nohibernate')
            and not shlex.split(x)[0].startswith(('resume=', 'resume_offset='))]
    return kept + [f'resume=UUID={uuid}', f'resume_offset={offset}']


def swap_fstab(text, uuid):
    """Replace only our two entries, rejecting an unrelated /swap mount."""
    lines = []
    for line in text.splitlines():
        fields = line.split()
        if fields and not fields[0].startswith('#'):
            if len(fields) < 4:
                raise ValueError('Malformed fstab entry')
            if fields[1] == '/swap':
                if (fields[0] != f'UUID={uuid}' or fields[2] != 'btrfs'
                        or 'subvol=@swap' not in fields[3].split(',')):
                    raise ValueError('Existing /swap fstab entry belongs to another mount')
                continue
            if fields[0] == SWAP:
                if fields[2] != 'swap':
                    raise ValueError('Existing hibernation swap entry is not swap')
                continue
        lines.append(line)
    lines.extend([f'UUID={uuid}\t/swap\tbtrfs\tdefaults,noatime,subvol=@swap\t0 0',
                  f'{SWAP}\tnone\tswap\tsw,pri=100\t0 0'])
    return '\n'.join(lines) + '\n'


class Hibernation:
    def __init__(self, root=Path('/')):
        # A fixture root is for unit tests only; the CLI always uses the real OS.
        self.root = Path(root)

    def path(self, name):
        return self.root / name.lstrip('/')

    def read(self, name):
        return self.path(name).read_text()

    def run(self, *args):
        return subprocess.run(args, check=True, text=True, capture_output=True).stdout.strip()

    def write(self, name, text, mode=0o644, backup=True):
        path = self.path(name)
        if path.is_symlink():
            raise ValueError(f'Refusing to replace symlink: {name}')
        if path.exists() and path.read_text() == text and (path.stat().st_mode & 0o777) == mode:
            return False
        copy = path.with_name(path.name + '.pre-hibernation')
        if backup and path.exists() and not copy.exists():
            shutil.copy2(path, copy)
        atomic_text(path, text, mode)
        return True

    def mount_info(self, point, field):
        return self.run('findmnt', '-n', '-o', field, '--mountpoint', point)

    def initramfs_generator(self):
        """Return 'initramfs-tools' or 'dracut', whichever builds this system's initramfs."""
        if self.path(INITRAMFS_TOOLS_RESUME).is_file():
            if 'resume_offset' not in self.read(INITRAMFS_TOOLS_RESUME):
                raise ValueError('Install initramfs-tools with swap-file resume support first')
            return 'initramfs-tools'
        if any(self.path(DRACUT_MODULES).glob('*resume')):
            return 'dracut'
        raise ValueError('Install initramfs-tools, or dracut with its resume module, first')

    def preflight(self):
        self.initramfs = self.initramfs_generator()
        tools = ('lsinitramfs',) if self.initramfs == 'initramfs-tools' else ('dracut', 'lsinitrd')
        for command in ('btrfs', 'findmnt', 'mount', 'umount', 'swapon', 'blkid', 'kernelstub',
                        'update-initramfs', *tools, 'systemctl', 'systemd-analyze'):
            if not shutil.which(command):
                raise ValueError(f'Required command missing: {command}')
        if not re.search(r'^ID=(?:pop|"pop")$', self.read('/etc/os-release'), re.MULTILINE):
            raise ValueError('Automatic hibernation requires Pop!_OS with kernelstub/systemd-boot')
        if self.mount_info('/', 'FSTYPE') != 'btrfs' or self.mount_info('/', 'FSROOT') != '/@':
            raise ValueError('Boot the installed Btrfs root subvolume @ first')
        if 'disk' not in self.read('/sys/power/state').split():
            raise ValueError('The running kernel does not support hibernation (disk sleep)')
        lockdown = self.path('/sys/kernel/security/lockdown')
        if lockdown.exists() and '[none]' not in lockdown.read_text():
            raise ValueError('Kernel lockdown prevents hibernation; check Secure Boot settings')
        if not {'platform', 'shutdown'}.intersection(
                self.read('/sys/power/disk').replace('[', '').replace(']', '').split()):
            raise ValueError('The kernel provides no supported hibernation power-off mode')
        version = re.search(r'v(\d+)\.(\d+)', self.run('btrfs', 'version'))
        if not version or tuple(map(int, version.groups())) < (6, 1):
            raise ValueError('Hibernation requires btrfs-progs >= 6.1 (mkswapfile/map-swapfile)')
        if self.mount_info('/boot/efi', 'FSTYPE') != 'vfat':
            raise ValueError('Mount the systemd-boot EFI partition at /boot/efi first')
        self.config = json.loads(self.read('/etc/kernelstub/configuration'))
        esp = self.config.get('user', {}).get('esp_path',
                    self.config.get('default', {}).get('esp_path', '/boot/efi'))
        if esp != '/boot/efi':
            raise ValueError('Only kernelstub ESP path /boot/efi is supported')
        self.read('/boot/efi/loader/entries/Pop_OS-current.conf')
        for unit in ('hibernate.target', 'systemd-hibernate.service'):
            state = self.run('systemctl', 'show', '-p', 'LoadState', '--value', unit)
            if state != 'loaded':
                raise ValueError(f'{unit} is {state}; resolve its availability/mask first')
        self.uuid = self.mount_info('/', 'UUID')
        if not re.fullmatch(r'[a-fA-F0-9-]+', self.uuid):
            raise ValueError('Invalid Btrfs filesystem UUID')
        devices = re.search(r'Total devices\s+(\d+)', self.run('btrfs', 'filesystem', 'show', '/'))
        if not devices or devices[1] != '1':
            raise ValueError('Automatic hibernation supports single-device Btrfs only')
        self.size = swap_size(self.read('/proc/meminfo'))
        self.fstab = swap_fstab(self.read('/etc/fstab'), self.uuid)
        resume_options(self.config, self.uuid, 1)  # Validate before allocating swap.
        for name in ('/etc/fstab', '/etc/kernelstub/configuration',
                     INITRAMFS_TOOLS_CONF, DRACUT_CONF,
                     '/etc/systemd/sleep.conf.d/90-pop-hibernation.conf',
                     '/etc/systemd/logind.conf.d/90-lid-close.conf', POLKIT_RULE, S2H_HOOK, '/swap'):
            if self.path(name).is_symlink():
                raise ValueError(f'Refusing a symlink at {name}')
        swap = self.path(SWAP)
        if swap.is_symlink() or (swap.exists() and not swap.is_file()):
            raise ValueError(f'{SWAP} must be a regular file, not a symlink')
        if swap.exists() and swap.stat().st_size < self.size:
            raise ValueError(f'{SWAP} is too small; need {self.size // GIB} GiB. '
                             'Existing swap is never automatically resized or deactivated.')
        needed = 0 if swap.exists() else self.size
        if shutil.disk_usage(self.path('/')).free < needed + 2 * GIB:
            raise ValueError(f'Need {needed // GIB + 2} GiB free, including filesystem headroom')
        print(f'Hibernation preflight passed: {self.size // GIB} GiB swap required.')

    @contextmanager
    def top_level(self):
        # Snapper's TMPDIR is recursively cleaned by its exit trap. Keep mounted
        # filesystems outside it, even when an unmount fails.
        directory = Path(tempfile.mkdtemp(prefix='pop-swap-', dir='/run'))
        mounted = False
        try:
            self.run('mount', '-t', 'btrfs', '-o', 'subvolid=5', f'UUID={self.uuid}', str(directory))
            mounted = True
            yield directory
        finally:
            if mounted:
                self.run('umount', str(directory))
            # Never recursively remove a mountpoint: an unsuccessful unmount
            # must leave both the mount and its data intact for inspection.
            directory.rmdir()

    def ensure_subvolume(self):
        directory = self.path('/swap')
        if directory.is_symlink():
            raise ValueError('/swap must not be a symlink')
        # Query without --mountpoint: an unmounted directory resolves to /.
        mounted = self.run('findmnt', '-n', '-o', 'TARGET', '--target', str(directory)) if directory.exists() else ''
        if mounted == '/swap':
            if (self.mount_info('/swap', 'FSROOT') != '/@swap'
                    or self.mount_info('/swap', 'UUID') != self.uuid):
                raise ValueError('/swap is mounted from another subvolume or filesystem')
            return False
        if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
            raise ValueError('The unmounted /swap directory must be empty')
        with self.top_level() as top:
            subvolume = top / '@swap'
            if subvolume.is_symlink():
                raise ValueError('@swap must not be a symlink')
            if subvolume.exists():
                self.run('btrfs', 'subvolume', 'show', str(subvolume))
            else:
                self.run('btrfs', 'subvolume', 'create', str(subvolume))
        directory.mkdir(mode=0o700, exist_ok=True)
        self.run('mount', '-t', 'btrfs', '-o', 'subvol=@swap,noatime',
                 f'UUID={self.uuid}', str(directory))
        if self.mount_info('/swap', 'FSROOT') != '/@swap' or self.mount_info('/swap', 'UUID') != self.uuid:
            raise ValueError('Mounted swap subvolume did not match the requested filesystem')
        return True

    def _verify_boot_entry(self, offset):
        entry = self.read('/boot/efi/loader/entries/Pop_OS-current.conf')
        options = [line.split()[1:] for line in entry.splitlines()
                   if line.split() and line.split()[0] == 'options']
        expected = {f'resume=UUID={self.uuid}', f'resume_offset={offset}'}
        if len(options) != 1 or not expected.issubset(options[0]):
            raise ValueError('The current EFI boot entry is missing the configured resume parameters')
        resume = [x for x in options[0] if x.startswith(('resume=', 'resume_offset='))]
        if len(resume) != 2 or 'noresume' in options[0] or 'nohibernate' in options[0]:
            raise ValueError('The EFI boot entry contains conflicting resume parameters')
        images = [line.split() for line in entry.splitlines()
                  if line.split() and line.split()[0] in ('linux', 'initrd')]
        if sum(line[0] == 'linux' for line in images) != 1 or not any(line[0] == 'initrd' for line in images):
            raise ValueError('The EFI boot entry needs a kernel and initramfs')
        has_resume = False
        for image in images:
            if len(image) != 2 or '..' in Path(image[1]).parts:
                raise ValueError('Invalid EFI boot image path')
            path = self.path('/boot/efi/' + image[1].lstrip('/'))
            if not path.is_file() or path.stat().st_size == 0:
                raise ValueError(f'Missing EFI boot image: {path}')
            if image[0] == 'initrd':
                has_resume |= self._initramfs_resumes(path)
        if not has_resume:
            raise ValueError('The EFI initramfs is missing its early-boot resume support'
                             + (' (systemd-hibernate-resume)' if self.initramfs == 'dracut' else ''))

    def _initramfs_resumes(self, path):
        if self.initramfs == 'dracut':
            # lsinitrd prints `ls -l`-style lines; the path is a whole field.
            fields = set(self.run('lsinitrd', str(path)).split())
            return all(name in fields for name in DRACUT_RESUME_FILES)
        return 'scripts/local-premount/resume' in self.run('lsinitramfs', str(path)).splitlines()

    def _boot_entry_valid(self, offset):
        try:
            self._verify_boot_entry(offset)
            return True
        except (ValueError, OSError, subprocess.CalledProcessError):
            return False

    def configure(self):
        self.preflight()
        subvol_changed = self.ensure_subvolume()
        swap_dir = self.path('/swap')
        if (swap_dir.stat().st_mode & 0o777) != 0o700:
            os.chmod(swap_dir, 0o700)
            subvol_changed = True
        swap = self.path(SWAP)
        if swap.is_symlink() or (swap.exists() and not swap.is_file()):
            raise ValueError('Refusing an invalid existing swap file')
        swap_changed = False
        if not swap.exists():
            # mkswapfile sets NOCOW, disables compression, allocates and formats.
            self.run('btrfs', 'filesystem', 'mkswapfile', '--size', str(self.size), str(swap))
            swap_changed = True
        if swap.stat().st_size < self.size:
            raise ValueError('Existing @swap file is too small; it was preserved without resizing')
        if (swap.stat().st_mode & 0o777) != 0o600:
            os.chmod(swap, 0o600)
            swap_changed = True
        if self.run('blkid', '-p', '-s', 'TYPE', '-o', 'value', str(swap)) != 'swap':
            raise ValueError('Existing file is not formatted as swap; refusing to overwrite it')
        offset = self.run('btrfs', 'inspect-internal', 'map-swapfile', '-r', str(swap))
        if not offset.isdecimal() or int(offset) <= 0:
            raise ValueError('Btrfs did not return a valid resume offset')
        active = self.run('swapon', '--show=NAME', '--noheadings', '--raw').splitlines()
        if str(swap) not in active:
            self.run('swapon', '--priority', '100', str(swap))
            swap_changed = True
        active = self.run('swapon', '--show=NAME', '--noheadings', '--raw').splitlines()
        if str(swap) not in active:
            raise ValueError('Hibernation swap did not become active')
        fstab_changed = self.write('/etc/fstab', self.fstab)
        self.run('findmnt', '--verify', '--tab-file', str(self.path('/etc/fstab')))
        if self.initramfs == 'dracut':
            resume_changed = self.write(DRACUT_CONF, DRACUT_CONF_TEXT)
        else:
            resume_changed = self.write(INITRAMFS_TOOLS_CONF, f'RESUME=UUID={self.uuid}\n')
        # The lid uses suspend-then-hibernate: suspend first, hibernate after
        # HIBERNATE_DELAY. If hibernation fails, the machine stays suspended
        # instead of staying awake.
        sleep_changed = self.write('/etc/systemd/sleep.conf.d/90-pop-hibernation.conf',
                                   '[Sleep]\nAllowHibernation=yes\nAllowSuspendThenHibernate=yes\n'
                                   f'HibernateMode=platform shutdown\nHibernateDelaySec={HIBERNATE_DELAY}\n')
        sleep = self.run('systemd-analyze', 'cat-config', 'systemd/sleep.conf')
        section, settings = '', {}
        for line in sleep.splitlines():
            line = line.strip()
            if line.startswith('[') and line.endswith(']'):
                section = line
            elif section == '[Sleep]' and '=' in line and not line.startswith(('#', ';')):
                key, value = line.split('=', 1)
                settings[key.strip()] = value.strip()
        if settings.get('AllowHibernation', '').lower() not in ('yes', 'true', '1', 'on'):
            raise ValueError('Another systemd sleep drop-in overrides AllowHibernation; resolve it and rerun')
        if settings.get('AllowSuspendThenHibernate', 'yes').lower() not in ('yes', 'true', '1', 'on'):
            raise ValueError('Another systemd sleep drop-in overrides AllowSuspendThenHibernate; resolve it and rerun')
        # Keeps suspend-then-hibernate from staying awake when the RTC wakes it
        # just before systemd's timer expires (systemd#38193). Sleep hooks are
        # read on each sleep, so no reload is needed.
        hook_changed = self.write(S2H_HOOK, S2H_HOOK_TEXT, mode=0o755, backup=False)
        # Retain unrelated options and replace old resume/noresume parameters.
        self.config.setdefault('user', {})['kernel_options'] = resume_options(self.config, self.uuid, offset)
        kernelstub_changed = self.write('/etc/kernelstub/configuration', json.dumps(self.config, indent=2) + '\n')
        if resume_changed or kernelstub_changed or not self._boot_entry_valid(offset):
            boot_changed = True
            self.run('update-initramfs', '-u', '-k', 'all')
            # Refresh the ESP only after the new initramfs images have been built.
            self.run('kernelstub', '-e', '/boot/efi', '-l', '-f')
            self._verify_boot_entry(offset)
        else:
            boot_changed = False
        # Install lid policy only after validating the persistent resume setup.
        # Reboot applies it without restarting logind in the desktop session.
        lid_changed = self.write('/etc/systemd/logind.conf.d/90-lid-close.conf',
                                 '[Login]\nHandleLidSwitch=suspend-then-hibernate\n'
                                 'HandleLidSwitchExternalPower=suspend\nHandleLidSwitchDocked=ignore\n')
        # polkitd watches its rules directories, so the rule applies immediately.
        polkit_changed = self.write(POLKIT_RULE, POLKIT_RULE_TEXT)
        if fstab_changed or sleep_changed or lid_changed:
            self.run('systemctl', 'daemon-reload')
        changed = any((subvol_changed, swap_changed, fstab_changed, resume_changed,
                       sleep_changed, hook_changed, kernelstub_changed, boot_changed, lid_changed,
                       polkit_changed))
        if changed:
            print(f'Hibernation configured: {SWAP} ({swap.stat().st_size // GIB} GiB).')
            print('Reboot to load the new resume settings, then test: systemctl hibernate')
            print(f'After reboot, closing the lid on battery suspends and then hibernates after {HIBERNATE_DELAY}; '
                  'on AC it suspends, and docked it is ignored.')
            print('A successful hibernate/resume cycle is required to verify hardware and driver support.')
        else:
            print(f'Hibernation already configured: {SWAP} ({swap.stat().st_size // GIB} GiB).')
        return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Run preflight without changing the system')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Run with sudo from the installed OS')
    try:
        with open('/run/pop-btrfs-hibernation.lock', 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            setup = Hibernation()
            if args.check:
                setup.preflight()
            else:
                setup.configure()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        detail = error.stderr if isinstance(error, subprocess.CalledProcessError) else str(error)
        parser.exit(1, f'Hibernation setup failed: {detail}\n'
                    'Existing swap is preserved. Resolve the error and rerun before trying hibernation.\n')


if __name__ == '__main__':
    main()
