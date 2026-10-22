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
import tarfile
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


def extract_zip(source, destination):
    root = Path(destination)
    root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source) as archive:
        # Check all link destinations before extracting anything.
        for member in archive.infolist():
            target = contained(root, member.filename)
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode):
                link = archive.read(member).decode()
                if not (target.parent / link).resolve().is_relative_to(root.resolve()):
                    raise ValueError(f'Unsafe archive symlink: {member.filename}')
        for member in archive.infolist():
            target = contained(root, member.filename)
            mode = member.external_attr >> 16
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                if stat.S_ISLNK(mode):
                    target.symlink_to(archive.read(member).decode())
                else:
                    with archive.open(member) as src, target.open('wb') as dst:
                        shutil.copyfileobj(src, dst)
                    target.chmod(stat.S_IMODE(mode) & 0o777 if mode else 0o644)


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
    import tempfile
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('zip', 'tar', 'makeself', 'sw-jre', 'response', 'wrapper', 'desktop'))
    parser.add_argument('arguments', nargs='+')
    args = parser.parse_args()
    if args.operation == 'tar':
        with tarfile.open(args.arguments[0], 'r:*') as archive:
            extract_tar(archive, args.arguments[1])
    else:
        operations = {'zip': extract_zip, 'makeself': makeself, 'sw-jre': sw_jre,
                      'response': response, 'wrapper': wrapper, 'desktop': desktop}
        operations[args.operation](*args.arguments)


if __name__ == '__main__':
    main()
