#!/usr/bin/env python3
"""Configure hibernation on the installed Pop!_OS Btrfs/systemd-boot layout.

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

    def write(self, name, text, mode=0o644):
        path = self.path(name)
        if path.is_symlink():
            raise ValueError(f'Refusing to replace symlink: {name}')
        backup = path.with_name(path.name + '.pre-hibernation')
        if path.exists() and not backup.exists():
            shutil.copy2(path, backup)
        atomic_text(path, text, mode)

    def mount_info(self, point, field):
        return self.run('findmnt', '-n', '-o', field, '--mountpoint', point)

    def preflight(self):
        for command in ('btrfs', 'findmnt', 'mount', 'umount', 'swapon', 'blkid',
                        'kernelstub', 'update-initramfs', 'lsinitramfs', 'systemctl', 'systemd-analyze'):
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
        if 'resume_offset' not in self.read('/usr/share/initramfs-tools/scripts/local-premount/resume'):
            raise ValueError('Install initramfs-tools with swap-file resume support first')
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
                     '/etc/initramfs-tools/conf.d/resume',
                     '/etc/systemd/sleep.conf.d/90-pop-hibernation.conf',
                     '/etc/systemd/logind.conf.d/90-lid-close.conf', '/swap'):
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
            return
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

    def configure(self):
        self.preflight()
        self.ensure_subvolume()
        os.chmod(self.path('/swap'), 0o700)
        swap = self.path(SWAP)
        if swap.is_symlink() or (swap.exists() and not swap.is_file()):
            raise ValueError('Refusing an invalid existing swap file')
        if not swap.exists():
            # mkswapfile sets NOCOW, disables compression, allocates and formats.
            self.run('btrfs', 'filesystem', 'mkswapfile', '--size', str(self.size), str(swap))
        if swap.stat().st_size < self.size:
            raise ValueError('Existing @swap file is too small; it was preserved without resizing')
        os.chmod(swap, 0o600)
        if self.run('blkid', '-p', '-s', 'TYPE', '-o', 'value', str(swap)) != 'swap':
            raise ValueError('Existing file is not formatted as swap; refusing to overwrite it')
        offset = self.run('btrfs', 'inspect-internal', 'map-swapfile', '-r', str(swap))
        if not offset.isdecimal() or int(offset) <= 0:
            raise ValueError('Btrfs did not return a valid resume offset')
        active = self.run('swapon', '--show=NAME', '--noheadings', '--raw').splitlines()
        if str(swap) not in active:
            self.run('swapon', '--priority', '100', str(swap))
        active = self.run('swapon', '--show=NAME', '--noheadings', '--raw').splitlines()
        if str(swap) not in active:
            raise ValueError('Hibernation swap did not become active')
        self.write('/etc/fstab', self.fstab)
        self.run('findmnt', '--verify', '--tab-file', str(self.path('/etc/fstab')))
        self.write('/etc/initramfs-tools/conf.d/resume', f'RESUME=UUID={self.uuid}\n')
        self.write('/etc/systemd/sleep.conf.d/90-pop-hibernation.conf',
                   '[Sleep]\nAllowHibernation=yes\nHibernateMode=platform shutdown\n')
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
        # Retain unrelated options and replace old resume/noresume parameters.
        self.config.setdefault('user', {})['kernel_options'] = resume_options(self.config, self.uuid, offset)
        self.write('/etc/kernelstub/configuration', json.dumps(self.config, indent=2) + '\n')
        self.run('update-initramfs', '-u', '-k', 'all')
        # Refresh the ESP only after the new initramfs images have been built.
        self.run('kernelstub', '-e', '/boot/efi', '-l', '-f')
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
                listing = self.run('lsinitramfs', str(path)).splitlines()
                has_resume |= 'scripts/local-premount/resume' in listing
        if not has_resume:
            raise ValueError('The EFI initramfs is missing its early-boot resume script')
        # Install lid policy only after validating the persistent resume setup.
        # Reboot applies it without restarting logind in the desktop session.
        self.write('/etc/systemd/logind.conf.d/90-lid-close.conf',
                   '[Login]\nHandleLidSwitch=hibernate\n'
                   'HandleLidSwitchExternalPower=suspend\nHandleLidSwitchDocked=ignore\n')
        self.run('systemctl', 'daemon-reload')
        print(f'Hibernation configured: {SWAP} ({swap.stat().st_size // GIB} GiB).')
        print('Reboot to load the new resume settings, then test: systemctl hibernate')
        print('After reboot, lid close hibernates on battery, suspends on AC, and is ignored when docked.')
        print('A successful hibernate/resume cycle is required to verify hardware and driver support.')


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
