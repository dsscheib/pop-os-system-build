#!/usr/bin/env python3
"""Check this repo's pinned versions against upstream releases, and bump them.

  python3 scripts/check_pins.py                 # report every pin
  python3 scripts/check_pins.py --bump NAME...  # update the named pins in place
  python3 scripts/check_pins.py --bump-all      # update every pin that can be bumped

Bumping a download fetches the new file and records its SHA-256 (cross-checked
against GitHub's published asset digest when there is one). Review the diff
before committing: a bump trusts whatever upstream serves at that moment.
"""
import argparse
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

import yaml

ROOT = Path(__file__).resolve().parents[1]
DOWNLOADS = 'ansible/group_vars/all/downloads.yml'
DEFAULTS = 'ansible/group_vars/all/00-defaults.yml'
REQUIREMENTS = 'ansible/requirements.yml'
BOOTSTRAP = 'bootstrap.sh'
# Files that repeat a bootstrap PyPI pin as `package==version`.
PYPI_PIN_MIRRORS = ('.github/workflows/check.yml',)
ANTIGRAVITY_MANIFEST_URL = (
    'https://antigravity-cli-auto-updater-974169037036.us-central1.run.app/manifests/linux_amd64.json'
)
USER_AGENT = 'pop-os-system-build-pin-check'
TIMEOUT = 30


@dataclass
class Pin:
    name: str
    location: str           # download | bootstrap | bootstrap-version | default | collection
    key: str                # entry, variable prefix/name, defaults key, or collection name
    source: tuple           # how to find the latest release (see LOOKUPS)
    bumpable: bool = True
    note: str = ''
    also_defaults: tuple = field(default_factory=tuple)  # defaults keys that mirror the version


PINS = [
    Pin('font', 'download', 'font', ('github', 'ryanoasis/nerd-fonts', r'^JetBrainsMono\.zip$')),
    Pin('appimagelauncher', 'download', 'appimagelauncher',
        ('github', 'TheAssassin/AppImageLauncher', r'_amd64\.deb$')),
    Pin('saleae', 'download', 'saleae', ('saleae',)),
    Pin('synology', 'download', 'synology',
        ('synology', 'SynologyDriveClient', r'/Ubuntu/Installer/[^"]*x86_64\.deb$')),
    Pin('hyper_backup_explorer', 'download', 'hyper_backup_explorer',
        ('synology', 'HyperBackupExplorer', r'/Linux/x86_64/[^"]*\.tar\.bz2$')),
    Pin('antigravity_cli', 'download', 'antigravity_cli', ('antigravity',)),
    Pin('arduino_ide', 'download', 'arduino_ide',
        ('github', 'arduino/arduino-ide', r'_Linux_64bit\.zip$')),
    Pin('cosmic_swipe', 'download', 'cosmic_swipe',
        ('github', 'dsscheib/cosmic-swipe', r'-x86_64-linux\.tar\.gz$')),
    Pin('mise', 'bootstrap', 'MISE', ('github', 'jdx/mise', r'^mise-v[0-9.]+-linux-x64$')),
    Pin('uv', 'bootstrap', 'UV', ('github', 'astral-sh/uv', r'^uv-x86_64-unknown-linux-gnu\.tar\.gz$')),
    Pin('ansible-core', 'bootstrap-version', 'ANSIBLE_CORE_VERSION', ('pypi', 'ansible-core')),
    Pin('ansible-lint', 'bootstrap-version', 'ANSIBLE_LINT_VERSION', ('pypi', 'ansible-lint')),
    Pin('community.general', 'collection', 'community.general', ('galaxy', 'community/general')),
    Pin('ansible.posix', 'collection', 'ansible.posix', ('galaxy', 'ansible/posix')),
    Pin('pynvim', 'default', 'pynvim_version', ('pypi', 'pynvim')),
    Pin('kicad-python', 'default', 'kicad_python_version', ('pypi', 'kicad-python')),
    Pin('tpm', 'default', 'tpm_commit', ('commit', 'tmux-plugins/tpm')),
    Pin('snapper-rollback', 'default', 'snapper_rollback_commit', ('commit', 'jrabinow/snapper-rollback')),
    Pin('llvm', 'default', 'llvm_version', ('llvm',), bumpable=False,
        note='apt.llvm.org also carries pre-release branches; confirm the new major is released '
             '(releases.llvm.org), then set llvm_version in 00-defaults.yml'),
    Pin('kicad', 'default', 'kicad_release_series', ('kicad',), bumpable=False,
        note='each KiCad PPA has its own signing key; update kicad_release_series and the kicad_key '
             'URL and fingerprint in downloads.yml together'),
]


# ---------------------------------------------------------------- HTTP

def _require_https(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or not parsed.netloc:
        raise ValueError(f'Refusing non-HTTPS URL: {url}')
    return url


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Block redirects to non-HTTPS URLs before opening them and strip GitHub tokens cross-host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urljoin(req.full_url, newurl)
        _require_https(target)
        redirected = super().redirect_request(req, fp, code, msg, headers, target)
        if redirected is None:
            return None
        orig_host = urllib.parse.urlsplit(req.full_url).netloc.lower()
        dest_host = urllib.parse.urlsplit(redirected.full_url).netloc.lower()
        if orig_host != 'api.github.com' or dest_host != 'api.github.com':
            for hdr_map in (redirected.headers, redirected.unredirected_hdrs):
                for key in [k for k in hdr_map if k.lower() == 'authorization']:
                    del hdr_map[key]
        return redirected


_OPENER = urllib.request.build_opener(_SafeRedirectHandler)


def _request(url, method='GET'):
    parsed = urllib.parse.urlsplit(_require_https(url))
    headers = {'User-Agent': USER_AGENT}
    token = os.environ.get('GITHUB_TOKEN')
    if token and parsed.netloc.lower() == 'api.github.com':
        headers['Authorization'] = f'Bearer {token}'
    return urllib.request.Request(url, headers=headers, method=method)


def http_text(url):
    with _OPENER.open(_request(url), timeout=TIMEOUT) as response:
        _require_https(response.geturl())
        return response.read().decode('utf-8', 'replace')


def http_json(url):
    return json.loads(http_text(url))


def http_exists(url):
    try:
        with _OPENER.open(_request(url, 'HEAD'), timeout=TIMEOUT) as response:
            _require_https(response.geturl())
            return response.status == 200
    except urllib.error.HTTPError:
        return False


def http_final_url(url):
    with _OPENER.open(_request(url, 'HEAD'), timeout=TIMEOUT) as response:
        return _require_https(response.geturl())


def download(url, destination):
    """Stream url to destination and return its SHA-256 hex digest."""
    digest = hashlib.sha256()
    with _OPENER.open(_request(url), timeout=TIMEOUT) as response:
        _require_https(response.geturl())
        with open(destination, 'wb') as out:
            for chunk in iter(lambda: response.read(1 << 20), b''):
                digest.update(chunk)
                out.write(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------- lookups
# Each returns a dict: version (str), and for downloads url and optional digest.

def lookup_github(repo, asset_pattern):
    release = http_json(f'https://api.github.com/repos/{repo}/releases/latest')
    assets = [a for a in release.get('assets', []) if re.search(asset_pattern, a['name'])]
    if len(assets) != 1:
        raise RuntimeError(f'expected one asset matching {asset_pattern!r} in {repo} '
                           f'{release["tag_name"]}, found {len(assets)}')
    digest = assets[0].get('digest') or ''
    return {'version': release['tag_name'], 'url': assets[0]['browser_download_url'],
            'digest': digest.removeprefix('sha256:') if digest.startswith('sha256:') else ''}


def lookup_saleae():
    url = http_final_url('https://logic2api.saleae.com/download?os=linux&arch=x64')
    match = re.search(r'Logic-([0-9][0-9.]*)-linux-x64\.AppImage$', url)
    if not match:
        raise RuntimeError(f'unexpected Saleae download URL: {url}')
    return {'version': match[1], 'url': url}


def _version_key(version):
    return [int(part) for part in re.findall(r'\d+', version)]


def lookup_synology(product, asset_pattern):
    base = f'https://archive.synology.com/download/Utility/{product}'
    versions = set(re.findall(rf'/download/Utility/{product}/([0-9][0-9.]*-[0-9]+)', http_text(base)))
    if not versions:
        raise RuntimeError(f'no {product} versions listed at {base}')
    version = max(versions, key=_version_key)
    links = [u for u in re.findall(r'href="([^"]+)"', http_text(f'{base}/{version}'))
             if re.search(asset_pattern, u)]
    if len(links) != 1:
        raise RuntimeError(f'expected one {product} {version} download matching {asset_pattern!r}, found {len(links)}')
    return {'version': version, 'url': links[0]}


def lookup_antigravity():
    data = http_json(ANTIGRAVITY_MANIFEST_URL)
    for field_name in ('version', 'url', 'sha512'):
        if not data.get(field_name):
            raise RuntimeError(f'Antigravity manifest missing {field_name}')
    return {
        'version': str(data['version']).strip(),
        'url': str(data['url']).strip(),
        'digest': str(data['sha512']).strip().lower(),
        'digest_algo': 'sha512',
    }


def lookup_pypi(package):
    return {'version': http_json(f'https://pypi.org/pypi/{package}/json')['info']['version']}


def lookup_galaxy(collection):
    data = http_json(f'https://galaxy.ansible.com/api/v3/plugin/ansible/content/published/collections/index/{collection}/')
    return {'version': data['highest_version']['version']}


def lookup_commit(repo):
    return {'version': http_json(f'https://api.github.com/repos/{repo}/commits/HEAD')['sha']}


def os_codename():
    try:
        text = Path('/etc/os-release').read_text()
    except OSError:
        return 'noble'
    values = dict(re.findall(r'^([A-Z_]+)="?([^"\n]*)"?$', text, re.M))
    return values.get('UBUNTU_CODENAME') or values.get('VERSION_CODENAME') or 'noble'


def lookup_llvm(current):
    codename, newest = os_codename(), int(current)
    for major in range(int(current) + 1, int(current) + 4):
        if http_exists(f'https://apt.llvm.org/{codename}/dists/llvm-toolchain-{codename}-{major}/Release'):
            newest = major
    return {'version': str(newest)}


def lookup_kicad(current):
    codename, newest = os_codename(), current
    major = int(float(current))
    for candidate in range(major + 1, major + 3):
        series = f'{candidate}.0'
        if http_exists(f'https://ppa.launchpadcontent.net/kicad/kicad-{series}-releases/ubuntu/dists/{codename}/Release'):
            newest = series
    return {'version': newest}


LOOKUPS = {
    'github': lookup_github, 'saleae': lookup_saleae, 'synology': lookup_synology,
    'antigravity': lookup_antigravity, 'pypi': lookup_pypi, 'galaxy': lookup_galaxy,
    'commit': lookup_commit, 'llvm': lookup_llvm, 'kicad': lookup_kicad,
}


def lookup(pin, current):
    kind, *args = pin.source
    if kind in ('llvm', 'kicad'):
        args = [current]
    return LOOKUPS[kind](*args)


# ---------------------------------------------------------------- reading pins

def read(root, relative):
    return (root / relative).read_text()


def current_value(root, pin):
    """Return the pin's current version string, or None when this checkout doesn't have it."""
    if pin.location == 'download':
        entry = (yaml.safe_load(read(root, DOWNLOADS)) or {}).get('pinned_downloads', {}).get(pin.key)
        return None if entry is None else str(entry['version'])
    if pin.location in ('bootstrap', 'bootstrap-version'):
        var = f'{pin.key}_VERSION' if pin.location == 'bootstrap' else pin.key
        match = re.search(rf'^{var}="([^"]*)"$', read(root, BOOTSTRAP), re.M)
        return match[1] if match else None
    if pin.location == 'default':
        value = (yaml.safe_load(read(root, DEFAULTS)) or {}).get(pin.key)
        return None if value is None else str(value)
    if pin.location == 'collection':
        for item in (yaml.safe_load(read(root, REQUIREMENTS)) or {}).get('collections', []):
            if item.get('name') == pin.key:
                return str(item.get('version'))
        return None
    raise ValueError(f'unknown location {pin.location}')


# ---------------------------------------------------------------- editing pins

_VERSION_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._+~-]*')
_SAFE_URL_RE = re.compile(r'https://[A-Za-z0-9._~:/?#@!&+=%,;-]+')
_SHA256_RE = re.compile(r'[0-9a-f]{64}')


def _validate_version(value, what='version'):
    if not isinstance(value, str) or not _VERSION_RE.fullmatch(value):
        raise ValueError(f'Unsafe {what}: {value!r}')
    return value


def _validate_url(value, what='url'):
    if not isinstance(value, str):
        raise ValueError(f'Unsafe {what}: {value!r}')
    _require_https(value)
    if not _SAFE_URL_RE.fullmatch(value):
        raise ValueError(f'Unsafe {what}: {value!r}')
    return value


def _validate_sha256(value, what='sha256'):
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError(f'Unsafe {what}: {value!r}')
    return value


def _replace_once(text, pattern, replacement, what):
    new, count = re.subn(pattern, lambda _: replacement, text, count=1, flags=re.M)
    if count != 1:
        raise RuntimeError(f'could not find {what}')
    return new


def set_download_fields(text, entry, fields):
    """Set `field: "value"` lines inside one pinned_downloads entry, keeping everything else."""
    block = re.search(rf'^  {re.escape(entry)}:\n((?:    .*\n?)*)', text, re.M)
    if not block:
        raise RuntimeError(f'download entry {entry!r} not found')
    body = block[1]
    for name, value in fields.items():
        if name == 'url':
            _validate_url(value, f'{entry}.{name}')
        else:
            _validate_version(value, f'{entry}.{name}')
        body = _replace_once(body, rf'^    {re.escape(name)}: .*$', f'    {name}: "{value}"', f'{entry}.{name}')
    return text[:block.start(1)] + body + text[block.end(1):]


def set_default(text, key, value):
    """Set a top-level `key: value` line, keeping the existing quoting style."""
    _validate_version(value, key)
    match = re.search(rf'^{re.escape(key)}: (.*)$', text, re.M)
    if not match:
        raise RuntimeError(f'default {key!r} not found')
    quoted = match[1].strip().startswith('"')
    return _replace_once(text, rf'^{re.escape(key)}: .*$', f'{key}: "{value}"' if quoted else f'{key}: {value}', key)


def set_shell_var(text, var, value):
    if var.endswith('_URL'):
        _validate_url(value, var)
    elif var.endswith('_SHA256'):
        _validate_sha256(value, var)
    else:
        _validate_version(value, var)
    return _replace_once(text, rf'^{re.escape(var)}=".*"$', f'{var}="{value}"', var)


def set_collection_version(text, name, value):
    _validate_version(value, name)
    pattern = rf'(^  - name: {re.escape(name)}\n    version: )"[^"]*"'
    new, count = re.subn(pattern, lambda m: f'{m[1]}"{value}"', text, count=1, flags=re.M)
    if count != 1:
        raise RuntimeError(f'collection {name!r} not found')
    return new


def deb_version(path):
    result = subprocess.run(['dpkg-deb', '-f', str(path), 'Version'], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _file_digest(path, algo):
    digest = hashlib.new(algo)
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_and_hash(info, workdir, fetch=download):
    """Download info['url'] and return (sha256, local path), verifying any published digest."""
    _require_https(info['url'])
    raw_name = Path(urllib.parse.urlparse(info['url']).path).name
    filename = raw_name if raw_name and raw_name not in ('.', '..') else 'download'
    path = Path(workdir) / filename
    sha256 = fetch(info['url'], path)
    if info.get('digest'):
        algo = info.get('digest_algo', 'sha256')
        actual = sha256 if algo == 'sha256' else _file_digest(path, algo)
        if info['digest'] != actual:
            label = {'sha256': 'SHA-256', 'sha512': 'SHA-512'}.get(algo, algo.upper())
            raise RuntimeError(f'{label} mismatch for {info["url"]}: upstream publishes {info["digest"]}, downloaded {actual}')
    return sha256, path


def bump(root, pin, info, fetch=download):
    """Apply info (from lookup) to the repo files. Returns the list of files changed."""
    version = _validate_version(info['version'], f'{pin.name}.version')
    changed = {}

    def edit(relative, function, *args):
        text = changed.get(relative, read(root, relative))
        changed[relative] = function(text, *args)

    if pin.location in ('download', 'bootstrap'):
        url = _validate_url(info['url'], f'{pin.name}.url')
        with tempfile.TemporaryDirectory(prefix='pop-pin-') as workdir:
            sha256, path = fetch_and_hash(info, workdir, fetch)
            _validate_sha256(sha256, f'{pin.name}.sha256')
            if pin.location == 'download':
                fields = {'version': version, 'url': url, 'sha256': sha256}
                entry = yaml.safe_load(read(root, DOWNLOADS))['pinned_downloads'][pin.key]
                if 'deb_version' in entry:
                    fields['deb_version'] = _validate_version(deb_version(path), f'{pin.name}.deb_version')
                edit(DOWNLOADS, set_download_fields, pin.key, fields)
                defaults = yaml.safe_load(read(root, DEFAULTS)) or {}
                for key in pin.also_defaults:
                    if key in defaults:
                        edit(DEFAULTS, set_default, key, version)
            else:
                for suffix, value in (('VERSION', version), ('URL', url), ('SHA256', sha256)):
                    edit(BOOTSTRAP, set_shell_var, f'{pin.key}_{suffix}', value)
    elif pin.location == 'bootstrap-version':
        old = current_value(root, pin)
        edit(BOOTSTRAP, set_shell_var, pin.key, version)
        package = pin.source[1]
        for relative in PYPI_PIN_MIRRORS:
            if (root / relative).exists() and f'{package}=={old}' in read(root, relative):
                edit(relative, lambda text: text.replace(f'{package}=={old}', f'{package}=={version}'))
    elif pin.location == 'default':
        edit(DEFAULTS, set_default, pin.key, version)
    elif pin.location == 'collection':
        edit(REQUIREMENTS, set_collection_version, pin.key, version)

    for relative, text in changed.items():
        (root / relative).write_text(text)
    return sorted(changed)


# ---------------------------------------------------------------- commands

def short(value):
    return value[:12] if value and re.fullmatch(r'[0-9a-f]{40}', value) else value


def _is_commit(value):
    return bool(value and re.fullmatch(r'[0-9a-fA-F]{40}', value))


def _parse_version(value):
    cleaned = value.strip().lstrip('vV')
    match = re.match(r'^([0-9]+(?:[.\-][0-9]+)*)(?:[-~.+]?([a-zA-Z].*))?$', cleaned)
    if not match:
        return None
    nums = tuple(int(part) for part in re.findall(r'\d+', match.group(1)))
    pre_raw = match.group(2)
    if not pre_raw:
        return (nums, 1, ())
    pre_parts = tuple(
        (0, int(tok)) if tok.isdigit() else (1, tok.lower())
        for tok in re.findall(r'\d+|[a-zA-Z]+', pre_raw)
    )
    return (nums, 0, pre_parts)


def _compare_versions(candidate, current):
    if candidate == current:
        return 0
    if _is_commit(candidate) or _is_commit(current):
        return None
    pa, pb = _parse_version(candidate), _parse_version(current)
    if pa is None or pb is None:
        return None
    na, sa, ta = pa
    nb, sb, tb = pb
    width = max(len(na), len(nb))
    na = na + (0,) * (width - len(na))
    nb = nb + (0,) * (width - len(nb))
    ka, kb = (na, sa, ta), (nb, sb, tb)
    return -1 if ka < kb else (1 if ka > kb else 0)


def check(root, pins, lookup_fn=lookup):
    """Yield (pin, current, latest_info_or_None, status) for every pin present in this checkout."""
    for pin in pins:
        current = current_value(root, pin)
        if current is None:
            continue
        try:
            info = lookup_fn(pin, current)
        except Exception as error:  # report network and parsing problems per pin
            yield pin, current, None, f'check failed: {error}'
            continue
        cmp_res = _compare_versions(info['version'], current)
        if info['version'] == current:
            status = 'up to date'
        elif cmp_res is not None and cmp_res <= 0:
            status = f'up to date ({short(current)} is newer than {short(info["version"])})'
        elif pin.bumpable:
            status = f'update available: mise run pins --bump {pin.name}'
        else:
            status = f'update available, update by hand: {pin.note}'
        yield pin, current, info, status


def report(root, pins, lookup_fn=lookup, out=sys.stdout):
    rows = [(pin.name, short(current), short(info['version']) if info else '?', status)
            for pin, current, info, status in check(root, pins, lookup_fn)]
    widths = [max(len(r[i]) for r in rows + [('PIN', 'CURRENT', 'LATEST', '')]) for i in range(3)]
    print(f'{"PIN":<{widths[0]}}  {"CURRENT":<{widths[1]}}  {"LATEST":<{widths[2]}}  STATUS', file=out)
    for row in rows:
        print(f'{row[0]:<{widths[0]}}  {row[1]:<{widths[1]}}  {row[2]:<{widths[2]}}  {row[3]}', file=out)
    print('\nNot checked: resources/st-installers.sha256 (ST downloads need a sign-in), '
          'APT signing keys (an apply fails its checksum or fingerprint check if a vendor rotates one), '
          'and GitHub Actions in .github/workflows/.', file=out)
    return rows


def bump_named(root, names, lookup_fn=lookup, fetch=download, out=sys.stdout):
    by_name = {pin.name: pin for pin in PINS}
    unknown = [name for name in names if name not in by_name]
    if unknown:
        raise SystemExit(f'Unknown pin(s): {", ".join(unknown)}. Known: {", ".join(by_name)}')
    failures, touched = 0, set()
    for name in names:
        pin = by_name[name]
        current = current_value(root, pin)
        if current is None:
            print(f'{name}: not pinned in this checkout; skipped', file=out)
            continue
        if not pin.bumpable:
            print(f'{name}: cannot be bumped automatically: {pin.note}', file=out)
            failures += 1
            continue
        try:
            info = lookup_fn(pin, current)
            if info['version'] == current:
                print(f'{name}: already up to date ({short(current)})', file=out)
                continue
            cmp_res = _compare_versions(info['version'], current)
            if cmp_res is not None and cmp_res <= 0:
                print(f'{name}: already at newer version {short(current)} than {short(info["version"])}; skipping downgrade',
                      file=out)
                continue
            files = bump(root, pin, info, fetch)
        except Exception as error:
            print(f'{name}: bump failed: {error}', file=out)
            failures += 1
            continue
        touched.update(files)
        print(f'{name}: {short(current)} -> {short(info["version"])} ({", ".join(files)})', file=out)
    if touched:
        print('\nNext: review `git diff`, run `mise run check` and the container test, '
              'then `mise run plan` before applying.', file=out)
    return failures


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--bump', nargs='+', metavar='NAME', help='update these pins in place')
    group.add_argument('--bump-all', action='store_true', help='update every pin that can be bumped automatically')
    args = parser.parse_args(argv)
    if args.bump:
        return 1 if bump_named(ROOT, args.bump) else 0
    if args.bump_all:
        names = [pin.name for pin, _, info, status in check(ROOT, PINS)
                 if pin.bumpable and status.startswith('update available')]
        if not names:
            print('Every automatically bumpable pin is up to date.')
            return 0
        return 1 if bump_named(ROOT, names) else 0
    report(ROOT, PINS)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
