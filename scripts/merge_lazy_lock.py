#!/usr/bin/env python3
"""Sync a Neovim config Git repo while preserving/merging local lazy-lock.json updates."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys


def merge_lazy_lock(base, upstream, local):
    """Three-way merge of lazy-lock.json dicts; return `(merged_dict, conflicts)`."""
    merged = {}
    conflicts = 0
    for name in sorted(set(upstream.keys()) | set(local.keys())):
        if name in upstream:
            in_local = name in local
            local_changed = in_local and local[name] != base.get(name)
            upstream_unchanged = upstream[name] == base.get(name)
            if local_changed and upstream_unchanged:
                merged[name] = local[name]
            else:
                merged[name] = upstream[name]
                if local_changed and not upstream_unchanged and local[name] != upstream[name]:
                    conflicts += 1
        elif name not in base:
            merged[name] = local[name]
    return merged, conflicts


def format_lazy_lock(lock):
    """Format a merged lockfile using lazy.nvim's canonical single-line-per-plugin layout."""
    lines = ['{']
    entries = list(lock.items())
    for idx, (plugin, fields) in enumerate(entries):
        inner = ', '.join(
            f'{json.dumps(k)}: {json.dumps(v)}' for k, v in fields.items()
        )
        suffix = ',' if idx < len(entries) - 1 else ''
        lines.append(f'  {json.dumps(plugin)}: {{ {inner} }}{suffix}')
    lines.append('}')
    return '\n'.join(lines) + '\n'


def _git(repo_dir, *args, check=True):
    cmd = ['git']
    if repo_dir is not None:
        cmd.extend(['-C', str(repo_dir)])
    cmd.extend(args)
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def _sync_update(dest, ref):
    if ref:
        _git(dest, 'fetch', 'origin')
        _git(dest, 'checkout', '--detach', ref)
    else:
        _git(dest, 'pull', '--ff-only')


def _resolve_check_mode_changed(dest, old_head, ref):
    if ref:
        if re.fullmatch(r'[0-9a-fA-F]{7,40}', ref):
            return not old_head.lower().startswith(ref.lower())
        out = _git(
            dest, 'ls-remote', 'origin',
            f'refs/heads/{ref}', f'refs/tags/{ref}', f'refs/tags/{ref}^{{}}',
        ).stdout
        refs = {}
        for line in out.splitlines():
            parts = line.split(None, 1)
            if len(parts) == 2:
                refs[parts[1]] = parts[0]
        remote_head = (
            refs.get(f'refs/heads/{ref}')
            or refs.get(f'refs/tags/{ref}^{{}}')
            or refs.get(f'refs/tags/{ref}')
        )
        if not remote_head:
            fallback_line = _git(dest, 'ls-remote', 'origin', ref).stdout.strip()
            remote_head = fallback_line.split()[0] if fallback_line else ''
        return bool(remote_head and remote_head != old_head)

    upstream = _git(dest, 'rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{upstream}', check=False)
    upstream_name = upstream.stdout.strip() if upstream.returncode == 0 else ''
    if upstream_name.startswith('origin/'):
        target_ref = f'refs/heads/{upstream_name.removeprefix("origin/")}'
    else:
        target_ref = 'HEAD'
    remote_line = _git(dest, 'ls-remote', 'origin', target_ref).stdout.strip()
    remote_head = remote_line.split()[0] if remote_line else ''
    return bool(remote_head and remote_head != old_head)


def sync_repo(repo_url, dest_dir, *, ref='', check_mode=False):
    """Clone or fast-forward `dest_dir` from `repo_url`, merging `lazy-lock.json` if dirty."""
    dest = Path(dest_dir)
    if not dest.exists() and not dest.is_symlink():
        if check_mode:
            return {'changed': True, 'merged': False, 'conflicts': 0, 'head': ''}
        dest.parent.mkdir(parents=True, exist_ok=True)
        _git(None, 'clone', repo_url, str(dest))
        if ref:
            _git(dest, 'checkout', '--detach', ref)
        head = _git(dest, 'rev-parse', 'HEAD').stdout.strip()
        return {'changed': True, 'merged': False, 'conflicts': 0, 'head': head}

    if not (dest / '.git').is_dir():
        raise RuntimeError(f'{dest} is not a Git checkout; preserve or relocate it before retrying.')

    origin = _git(dest, 'remote', 'get-url', 'origin').stdout.strip()
    if origin != repo_url:
        raise RuntimeError(f'Existing repository has a different origin: {dest} ({origin})')

    status = _git(dest, 'status', '--porcelain').stdout.rstrip('\n')
    dirty_lock = status == ' M lazy-lock.json'
    if status and not dirty_lock:
        raise RuntimeError(f'Local uncommitted changes in {dest}; commit or stash them before updating.')

    old_head = _git(dest, 'rev-parse', 'HEAD').stdout.strip()
    if check_mode:
        changed = _resolve_check_mode_changed(dest, old_head, ref)
        return {'changed': changed, 'merged': False, 'conflicts': 0, 'head': old_head}

    merged = False
    conflicts = 0
    if dirty_lock:
        git_dir = Path(_git(dest, 'rev-parse', '--absolute-git-dir').stdout.strip())
        saved_path = git_dir / 'pop-workstation-lazy-lock.json'
        lock_path = dest / 'lazy-lock.json'
        saved_text = lock_path.read_text(encoding='utf-8')
        saved_path.write_text(saved_text, encoding='utf-8')
        try:
            _git(dest, 'checkout', '--', 'lazy-lock.json')
            _sync_update(dest, ref)
            new_head = _git(dest, 'rev-parse', 'HEAD').stdout.strip()
            diff_res = _git(dest, 'diff', '--quiet', old_head, new_head, '--', 'lazy-lock.json', check=False)
            if diff_res.returncode == 0:
                lock_path.write_text(saved_text, encoding='utf-8')
            elif not lock_path.exists():
                pass
            else:
                base_obj = json.loads(_git(dest, 'show', f'{old_head}:lazy-lock.json').stdout)
                upstream_obj = json.loads(lock_path.read_text(encoding='utf-8'))
                local_obj = json.loads(saved_text)
                merged_obj, conflicts = merge_lazy_lock(base_obj, upstream_obj, local_obj)
                lock_path.write_text(format_lazy_lock(merged_obj), encoding='utf-8')
                merged = True
        except Exception:
            lock_path.write_text(saved_text, encoding='utf-8')
            raise
    else:
        _sync_update(dest, ref)
        new_head = _git(dest, 'rev-parse', 'HEAD').stdout.strip()

    return {
        'changed': old_head != new_head,
        'merged': merged,
        'conflicts': conflicts,
        'head': new_head,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest='command', required=True)

    sync_cmd = subparsers.add_parser('sync-repo', help='Clone or update Neovim config repo')
    sync_cmd.add_argument('--repo', required=True)
    sync_cmd.add_argument('--dest', required=True)
    sync_cmd.add_argument('--ref', default='')
    sync_cmd.add_argument('--check', action='store_true')

    args = parser.parse_args(argv)
    try:
        if args.command == 'sync-repo':
            result = sync_repo(args.repo, args.dest, ref=args.ref, check_mode=args.check)
            print(json.dumps(result))
    except (OSError, RuntimeError, json.JSONDecodeError, subprocess.CalledProcessError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
