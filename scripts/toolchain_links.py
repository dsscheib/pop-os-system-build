#!/usr/bin/env python3
"""Select newest complete APT compiler majors and atomically link ~/.local/bin tools."""
import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def parse_pkgnames(output):
    """Return the set of package names from `apt-cache pkgnames` output."""
    return {line.strip() for line in output.splitlines() if line.strip()}


def parse_apt_candidate(policy_output, package):
    """Return the Candidate version for an exact package header, or None."""
    matched = False
    header = f'{package}:'
    for raw_line in policy_output.splitlines():
        stripped = raw_line.strip()
        if not matched:
            if stripped == header:
                matched = True
            continue
        if not raw_line.startswith(' ') and stripped.endswith(':'):
            break
        if stripped.startswith('Candidate:'):
            candidate = stripped.split(':', 1)[1].strip()
            if candidate and candidate != '(none)':
                return candidate
            return None
    return None


def select_latest_major(packages, pkgnames_output, policy_lookup):
    """Return the highest integer major N where every `<pkg>-<N>` has a candidate."""
    if not packages:
        raise ValueError('At least one base package name is required.')
    names = parse_pkgnames(pkgnames_output)
    prefix = f'{packages[0]}-'
    majors = set()
    for name in names:
        if name.startswith(prefix):
            suffix = name[len(prefix):]
            if suffix.isdigit():
                majors.add(int(suffix))

    for major in sorted(majors, reverse=True):
        if all(f'{base}-{major}' in names for base in packages):
            if all(policy_lookup(f'{base}-{major}') is not None for base in packages):
                return str(major)
    raise RuntimeError(
        f'No installable complete major found for packages: {" ".join(packages)}'
    )


def _run_apt_cache(*args):
    env = dict(os.environ, LC_ALL='C')
    proc = subprocess.run(
        ['apt-cache', *args],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout


def parse_link_specs(specs):
    """Parse `name` or `name=target` entries into `(name, target)` tuples."""
    parsed = []
    for spec in specs:
        if '=' in spec:
            name, target = spec.split('=', 1)
        else:
            name, target = spec, spec
        if not name or not target or '/' in name or '/' in target:
            raise ValueError(f'Invalid command link specification: {spec!r}')
        parsed.append((name, target))
    return parsed


def plan_links(major, bin_dir, dest_dir, specs):
    """Validate targets and existing destinations; return `[(dest_path, target_path)]` to update."""
    if not str(major).isdigit():
        raise ValueError(f'Major version must be numeric, got: {major!r}')
    bin_path = Path(bin_dir)
    dest_path = Path(dest_dir)
    updates = []

    for name, target in parse_link_specs(specs):
        versioned = bin_path / f'{target}-{major}'
        if not (versioned.is_file() and os.access(versioned, os.X_OK)):
            raise RuntimeError(f'Missing versioned tool: {versioned}')

        link_path = dest_path / name
        if os.path.lexists(link_path):
            if not os.path.islink(link_path):
                raise RuntimeError(f'Existing command needs manual review: {link_path}')
            existing = os.readlink(link_path)
            unversioned = str(bin_path / target)
            pattern = re.compile(rf'^{re.escape(unversioned)}-[0-9]+$')
            if existing != unversioned and not pattern.match(existing):
                raise RuntimeError(
                    f'Existing command link needs manual review: {link_path} -> {existing}'
                )
            if existing == str(versioned):
                continue

        updates.append((link_path, versioned))

    return updates


def sync_links(major, bin_dir, dest_dir, specs, *, check=False):
    """Validate and atomically synchronize versioned symlinks; return True if changes needed."""
    updates = plan_links(major, bin_dir, dest_dir, specs)
    if not updates:
        return False
    if check:
        return True

    dest_path = Path(dest_dir)
    dest_path.mkdir(parents=True, exist_ok=True)
    for link_path, target_path in updates:
        fd, tmp_name = tempfile.mkstemp(prefix=f'.{link_path.name}.', dir=dest_path)
        os.close(fd)
        os.unlink(tmp_name)
        try:
            os.symlink(str(target_path), tmp_name)
            os.replace(tmp_name, link_path)
        except Exception:
            if os.path.lexists(tmp_name):
                os.unlink(tmp_name)
            raise
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest='command', required=True)

    major_cmd = subparsers.add_parser('latest-major', help='Find newest complete APT package major')
    major_cmd.add_argument('packages', nargs='+')

    sync_cmd = subparsers.add_parser('sync-links', help='Sync unversioned symlinks to a major')
    sync_cmd.add_argument('--major', required=True)
    sync_cmd.add_argument('--bin-dir', default='/usr/bin')
    sync_cmd.add_argument('--dest-dir', required=True)
    sync_cmd.add_argument('--check', action='store_true')
    sync_cmd.add_argument('specs', nargs='+')

    args = parser.parse_args(argv)
    try:
        if args.command == 'latest-major':
            pkgnames = _run_apt_cache('pkgnames')
            major = select_latest_major(
                args.packages,
                pkgnames,
                lambda pkg: parse_apt_candidate(_run_apt_cache('policy', pkg), pkg),
            )
            print(major)
        elif args.command == 'sync-links':
            changed = sync_links(
                args.major,
                args.bin_dir,
                args.dest_dir,
                args.specs,
                check=args.check,
            )
            print('changed' if changed else 'unchanged')
    except (RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
