#!/usr/bin/env python3
"""Read installer containers and generate launchers without executing installers."""
import argparse
import gzip
import io
import mmap
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import xml.etree.ElementTree as ET
import zipfile


def contained(root, name):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts:
        raise ValueError(f'Unsafe archive path: {name}')
    target = root.joinpath(*path.parts)
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError(f'Archive path escapes extraction directory: {name}')
    return target


def _check_zip_link(resolved_root, target, link, name):
    if PurePosixPath(link).is_absolute():
        raise ValueError(f'Unsafe archive symlink: {name}')
    resolved_parent = target.parent.resolve()
    if not resolved_parent.is_relative_to(resolved_root):
        raise ValueError(f'Unsafe archive path: {name}')
    if not (resolved_parent / link).resolve().is_relative_to(resolved_root):
        raise ValueError(f'Unsafe archive symlink: {name}')


def _remove_escaping_symlinks(root, resolved_root):
    unsafe = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in dirnames + filenames:
            candidate = Path(dirpath) / name
            if not candidate.is_symlink():
                continue
            try:
                raw_link = os.readlink(candidate)
                if PurePosixPath(raw_link).is_absolute():
                    unsafe.append(candidate)
                    continue
                resolved = candidate.resolve()
                if not resolved.is_relative_to(resolved_root):
                    unsafe.append(candidate)
            except (OSError, RuntimeError):
                unsafe.append(candidate)
    for link_path in unsafe:
        link_path.unlink(missing_ok=True)
    if unsafe:
        raise ValueError(f'Unsafe archive symlink: {unsafe[0].relative_to(root)}')


def extract_zip(source, destination):
    root = Path(destination)
    root.mkdir(parents=True, exist_ok=True)
    resolved_root = root.resolve()
    with zipfile.ZipFile(source) as archive:
        # Check all link destinations before extracting anything.
        for member in archive.infolist():
            target = contained(root, member.filename)
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode):
                link = archive.read(member).decode()
                _check_zip_link(resolved_root, target, link, member.filename)
        for member in archive.infolist():
            target = contained(root, member.filename)
            resolved_parent = target.parent.resolve()
            if not resolved_parent.is_relative_to(resolved_root):
                raise ValueError(f'Archive path escapes extraction directory: {member.filename}')
            mode = member.external_attr >> 16
            if member.is_dir():
                if target.is_symlink():
                    target.unlink()
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.parent.resolve().is_relative_to(resolved_root):
                    raise ValueError(f'Archive path escapes extraction directory: {member.filename}')
                if target.is_symlink():
                    target.unlink()
                if stat.S_ISLNK(mode):
                    link = archive.read(member).decode()
                    _check_zip_link(resolved_root, target, link, member.filename)
                    target.symlink_to(link)
                else:
                    if not target.resolve().is_relative_to(resolved_root):
                        raise ValueError(f'Archive path escapes extraction directory: {member.filename}')
                    with archive.open(member) as src, target.open('wb') as dst:
                        shutil.copyfileobj(src, dst)
                    target.chmod(stat.S_IMODE(mode) & 0o777 if mode else 0o644)
    _remove_escaping_symlinks(root, resolved_root)


def extract_tar(archive, destination):
    root = Path(destination)
    root.mkdir(parents=True, exist_ok=True)
    for member in archive:
        target = contained(root, member.name)
        if member.isdev() or member.isfifo():
            raise ValueError(f'Unsupported archive entry: {member.name}')
        if member.issym() or member.islnk():
            base = target.parent if member.issym() else root
            if not (base / member.linkname).resolve().is_relative_to(root.resolve()):
                raise ValueError(f'Unsafe archive link: {member.name}')
        # No ownership restoration or setuid/setgid bits.
        member.mode &= 0o777
        if sys.version_info >= (3, 12):
            archive.extract(member, root, set_attrs=True, numeric_owner=False, filter='data')
        else:
            archive.extract(member, root, set_attrs=True, numeric_owner=False)


def makeself(source, destination):
    with open(source, 'rb') as stream:
        header = stream.read(32768)
        match = re.search(rb'head -n ([0-9]+) "\$0"', header)
        if not match:
            raise ValueError('Unsupported Makeself header')
        stream.seek(0)
        for _ in range(int(match[1])):
            if not stream.readline():
                raise ValueError('Truncated Makeself header')
        with tarfile.open(fileobj=stream, mode='r|*') as archive:
            extract_tar(archive, destination)


def sw_jre(source, destination):
    with open(source, 'rb') as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
        start = data.find(b'\nJRE_MARKER:\n')
        end = data.find(b'\nJAR_MARKER:\n', start + 1)
        if start < 0 or end < 0:
            raise ValueError('Bundled SW4STM32 JRE markers not found')
        start += len(b'\nJRE_MARKER:\n')
        with gzip.GzipFile(fileobj=io.BytesIO(data[start:end])) as payload:
            with tarfile.open(fileobj=payload, mode='r|') as archive:
                extract_tar(archive, destination)


def response(template, target, destination, jar):
    tree = ET.parse(template)
    for element in tree.iter('installpath'):
        element.text = target
    # The supplied archives use these exact classes and IDs. Refuse incompatible
    # versions before invoking Java, rather than silently skipping installer panels.
    with zipfile.ZipFile(jar) as archive:
        panels = archive.read('resources/panelsOrder')
        names = set(archive.namelist())
        for element in tree.getroot():
            if element.tag.replace('.', '/') + '.class' not in names:
                raise ValueError(f'Installer panel class missing: {element.tag}')
            if element.get('id', '').encode() not in panels:
                raise ValueError(f'Installer panel ID missing: {element.get("id")}')
    tree.write(destination, encoding='utf-8', xml_declaration=True)


def wrapper(destination, directory, executable, *environment):
    env = ''.join(f'export {key}={shlex.quote(value)}\n'
                  for key, value in (item.split('=', 1) for item in environment))
    content = ('#!/usr/bin/env bash\nset -euo pipefail\n' + env +
               f'export PATH={shlex.quote(str(Path(executable).parent))}:"$PATH"\n' +
               f'cd -- {shlex.quote(directory)}\nexec {shlex.quote(executable)} "$@"\n')
    atomic_text(destination, content, 0o755)


def desktop(destination, name, executable, directory, icon, wmclass, categories='Development;IDE;'):
    def value(text):
        return text.replace('\\', '\\\\').replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')
    quoted = executable.replace('\\', '\\\\\\\\').replace('"', '\\\\"').replace('`', '\\\\`').replace('$', '\\\\$').replace('%', '%%')
    content = ('[Desktop Entry]\nVersion=1.0\nType=Application\n' +
               f'Name={value(name)}\nExec="{quoted}" %F\nPath={value(directory)}\n' +
               f'Icon={value(icon)}\nTerminal=false\nCategories={value(categories)}\n' +
               (f'StartupWMClass={value(wmclass)}\n' if wmclass else ''))
    atomic_text(destination, content, 0o644)


def atomic_text(destination, content, mode):
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name + '.')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(content)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def verify_pgp(key_path, expected_fingerprint):
    """Verify `key_path` contains exactly one primary OpenPGP key with `expected_fingerprint`."""
    expected = expected_fingerprint.strip().upper()
    if not re.fullmatch(r'[0-9A-F]{40}', expected):
        raise ValueError(f'Invalid OpenPGP fingerprint: {expected_fingerprint}')
    path = Path(key_path)
    if not path.is_file():
        raise ValueError(f'OpenPGP key file missing: {path}')
    with tempfile.TemporaryDirectory(prefix='pop-gpg-') as gnupg_home:
        os.chmod(gnupg_home, 0o700)
        env = dict(os.environ, GNUPGHOME=gnupg_home, LC_ALL='C')
        proc = subprocess.run(
            ['gpg', '--batch', '--with-colons', '--show-keys', str(path)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise ValueError(f'Invalid OpenPGP key {path}: {detail}')
    pubs = 0
    subs = 0
    fprs = []
    for line in proc.stdout.splitlines():
        cols = line.split(':')
        record = cols[0] if cols else ''
        if record == 'pub':
            pubs += 1
        elif record == 'sub':
            subs += 1
        elif record == 'fpr' and len(cols) > 9:
            fprs.append(cols[9].upper())
    if pubs != 1 or subs != 0 or fprs != [expected]:
        raise ValueError(
            f'OpenPGP key {path} verification failed: pubs={pubs}, subs={subs}, fprs={fprs}'
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('zip', 'tar', 'makeself', 'sw-jre', 'response', 'wrapper', 'desktop', 'pgp-verify'))
    parser.add_argument('arguments', nargs='+')
    args = parser.parse_args()
    if args.operation == 'tar':
        with tarfile.open(args.arguments[0], 'r:*') as archive:
            extract_tar(archive, args.arguments[1])
    else:
        operations = {'zip': extract_zip, 'makeself': makeself, 'sw-jre': sw_jre,
                      'response': response, 'wrapper': wrapper, 'desktop': desktop,
                      'pgp-verify': verify_pgp}
        operations[args.operation](*args.arguments)


if __name__ == '__main__':
    main()
