"""Unit tests for scripts/check_pins.py (no network: lookups and downloads are faked)."""
import hashlib
import http.client
import importlib
import io
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
check_pins = importlib.import_module('check_pins')

DOWNLOADS = '''---
# Pinned downloads.

pinned_downloads:
  font:
    version: "v1.0.0"
    url: "https://example.com/v1.0.0/Font.zip"
    sha256: "aaaa"
  arduino_ide:
    version: "2.3.10"
    url: "https://example.com/2.3.10/arduino-ide_2.3.10_Linux_64bit.zip"
    sha256: "bbbb"
'''
DEFAULTS = '''---
llvm_version: 23
pynvim_version: "0.6.0"
arduino_ide_version: "2.3.10"
'''
REQUIREMENTS = '''---
collections:
  - name: community.general
    version: "9.0.0"
  - name: ansible.posix
    version: "1.5.0"
'''
BOOTSTRAP = '''#!/usr/bin/env bash
UV_VERSION="0.13.0"
UV_URL="https://example.com/0.13.0/uv.tar.gz"
UV_SHA256="cccc"
ANSIBLE_CORE_VERSION="2.22.0"
'''


class CheckPinsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pop-pins-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        files = {check_pins.DOWNLOADS: DOWNLOADS, check_pins.DEFAULTS: DEFAULTS,
                 check_pins.REQUIREMENTS: REQUIREMENTS, check_pins.BOOTSTRAP: BOOTSTRAP,
                 '.github/workflows/check.yml': 'pipx install --include-deps ansible-lint==26.11.0\npipx inject --include-apps ansible-lint ansible-core==2.22.0\n'}
        for relative, text in files.items():
            (self.root / relative).parent.mkdir(parents=True, exist_ok=True)
            (self.root / relative).write_text(text)
        self.payload = b'new release payload'
        self.sha = hashlib.sha256(self.payload).hexdigest()

    def pin(self, name):
        return next(p for p in check_pins.PINS if p.name == name)

    def fake_fetch(self, url, destination):
        Path(destination).write_bytes(self.payload)
        return self.sha

    def test_set_download_fields_edits_only_the_named_entry(self):
        text = check_pins.set_download_fields(DOWNLOADS, 'arduino_ide', {'version': '2.3.11', 'sha256': 'dddd'})
        data = yaml.safe_load(text)['pinned_downloads']
        self.assertEqual(data['arduino_ide']['version'], '2.3.11')
        self.assertEqual(data['arduino_ide']['sha256'], 'dddd')
        self.assertEqual(data['font'], {'version': 'v1.0.0', 'url': 'https://example.com/v1.0.0/Font.zip', 'sha256': 'aaaa'})
        self.assertIn('# Pinned downloads.', text)
        with self.assertRaises(RuntimeError):
            check_pins.set_download_fields(DOWNLOADS, 'missing', {'version': 'x'})

    def test_set_default_keeps_quoting_style(self):
        text = check_pins.set_default(DEFAULTS, 'llvm_version', '24')
        text = check_pins.set_default(text, 'pynvim_version', '0.7.0')
        self.assertIn('llvm_version: 24\n', text)
        self.assertIn('pynvim_version: "0.7.0"\n', text)

    def test_set_shell_var_and_collection_version(self):
        self.assertIn('UV_VERSION="0.14.0"', check_pins.set_shell_var(BOOTSTRAP, 'UV_VERSION', '0.14.0'))
        text = check_pins.set_collection_version(REQUIREMENTS, 'ansible.posix', '2.2.2')
        self.assertEqual(yaml.safe_load(text)['collections'][1]['version'], '2.2.2')
        self.assertEqual(yaml.safe_load(text)['collections'][0]['version'], '9.0.0')

    def test_report_marks_updates_and_skips_pins_missing_from_checkout(self):
        latest = {'font': 'v1.0.0', 'arduino_ide': '2.3.11', 'llvm': '24'}
        pins = [self.pin('font'), self.pin('arduino_ide'), self.pin('llvm'), self.pin('saleae')]
        rows = check_pins.report(self.root, pins, lambda pin, current: {'version': latest[pin.name]}, out=io.StringIO())
        status = {row[0]: row[3] for row in rows}
        self.assertEqual(status['font'], 'up to date')
        self.assertEqual(status['arduino_ide'], 'update available: mise run pins --bump arduino_ide')
        self.assertTrue(status['llvm'].startswith('update available, update by hand'))
        self.assertNotIn('saleae', status)  # not in this checkout's downloads.yml

    def test_failed_lookup_is_reported_per_pin(self):
        def broken(pin, current):
            raise RuntimeError('network down')
        rows = check_pins.report(self.root, [self.pin('pynvim')], broken, out=io.StringIO())
        self.assertEqual(rows[0][3], 'check failed: network down')

    def test_bump_download_records_hash_and_mirrors_defaults_version(self):
        info = {'version': '2.3.11', 'url': 'https://example.com/2.3.11/arduino-ide_2.3.11_Linux_64bit.zip'}
        out = io.StringIO()
        mirrored_pin = check_pins.Pin(
            'arduino_ide', 'download', 'arduino_ide',
            ('github', 'arduino/arduino-ide', r'_Linux_64bit\.zip$'),
            also_defaults=('arduino_ide_version',),
        )
        with mock.patch.object(check_pins, 'PINS', [mirrored_pin]):
            failures = check_pins.bump_named(self.root, ['arduino_ide'], lambda pin, current: info, self.fake_fetch, out)
        self.assertEqual(failures, 0, out.getvalue())
        entry = yaml.safe_load((self.root / check_pins.DOWNLOADS).read_text())['pinned_downloads']['arduino_ide']
        self.assertEqual(entry, {'version': '2.3.11', 'url': info['url'], 'sha256': self.sha})
        self.assertIn('arduino_ide_version: "2.3.11"', (self.root / check_pins.DEFAULTS).read_text())

    def test_bump_aborts_without_editing_when_published_digest_differs(self):
        info = {'version': '2.3.11', 'url': 'https://example.com/x.zip', 'digest': '0' * 64}
        out = io.StringIO()
        failures = check_pins.bump_named(self.root, ['arduino_ide'], lambda pin, current: info, self.fake_fetch, out)
        self.assertEqual(failures, 1)
        self.assertIn('SHA-256 mismatch', out.getvalue())
        self.assertEqual((self.root / check_pins.DOWNLOADS).read_text(), DOWNLOADS)

    def test_bump_bootstrap_download_and_pypi_mirrors(self):
        uv = {'version': '0.14.0', 'url': 'https://example.com/0.14.0/uv.tar.gz'}
        core = {'version': '2.23.0'}
        infos = {'uv': uv, 'ansible-core': core}
        failures = check_pins.bump_named(self.root, ['uv', 'ansible-core'],
                                         lambda pin, current: infos[pin.name], self.fake_fetch, io.StringIO())
        self.assertEqual(failures, 0)
        bootstrap = (self.root / check_pins.BOOTSTRAP).read_text()
        self.assertIn('UV_VERSION="0.14.0"', bootstrap)
        self.assertIn(f'UV_SHA256="{self.sha}"', bootstrap)
        self.assertIn('ANSIBLE_CORE_VERSION="2.23.0"', bootstrap)
        self.assertIn('ansible-core==2.23.0', (self.root / '.github/workflows/check.yml').read_text())

    def test_report_only_and_unknown_pins_are_refused(self):
        out = io.StringIO()
        self.assertEqual(check_pins.bump_named(self.root, ['llvm'], lambda p, c: {'version': '24'}, self.fake_fetch, out), 1)
        self.assertIn('cannot be bumped automatically', out.getvalue())
        self.assertEqual((self.root / check_pins.DEFAULTS).read_text(), DEFAULTS)
        with self.assertRaises(SystemExit):
            check_pins.bump_named(self.root, ['nonexistent'], out=io.StringIO())

    def test_every_pin_reads_from_the_real_repository(self):
        """Guards against the PINS table drifting from the files it edits."""
        missing = [pin.name for pin in check_pins.PINS
                   if check_pins.current_value(REPO, pin) is None]
        self.assertEqual(missing, [])

    def test_ci_workflow_pins_match_bootstrap(self):
        workflow = (REPO / '.github/workflows/check.yml').read_text()
        for pin_name, pkg in (('ansible-core', 'ansible-core'), ('ansible-lint', 'ansible-lint')):
            expected = check_pins.current_value(REPO, self.pin(pin_name))
            self.assertIn(f'{pkg}=={expected}', workflow)

    def test_rejects_non_https_urls_and_redirects(self):
        with self.assertRaises(ValueError):
            check_pins.http_text('http://example.com/file')

        handler = check_pins._SafeRedirectHandler()
        req = check_pins._request('https://example.com/start')
        with self.assertRaises(ValueError):
            handler.redirect_request(req, None, 302, 'Found', {}, 'http://insecure.example.com/redirected')
        with self.assertRaises(ValueError):
            handler.redirect_request(req, None, 302, 'Found', {}, 'file:///etc/passwd')

        # Redirect handling must reject http:// before parent.open is ever called.
        handler.parent = mock.MagicMock()
        fp = mock.MagicMock()
        redirect_headers = http.client.HTTPMessage()
        redirect_headers['Location'] = 'http://insecure.example.com/next'
        with self.assertRaises(ValueError):
            handler.http_error_302(req, fp, 302, 'Found', redirect_headers)
        handler.parent.open.assert_not_called()

        fake_resp = mock.MagicMock()
        fake_resp.__enter__.return_value = fake_resp
        fake_resp.geturl.return_value = 'http://insecure.example.com/redirected'
        with mock.patch.object(check_pins._OPENER, 'open', return_value=fake_resp):
            with self.assertRaises(ValueError):
                check_pins.http_text('https://example.com/start')
            with self.assertRaises(ValueError):
                check_pins.download('https://example.com/start', self.root / 'out.bin')

    def test_redirect_strips_github_authorization_header(self):
        handler = check_pins._SafeRedirectHandler()
        with mock.patch.dict('os.environ', {'GITHUB_TOKEN': 'secret-pat'}):
            req = check_pins._request('https://api.github.com/repos/owner/repo/releases/latest')
            self.assertEqual(req.get_header('Authorization'), 'Bearer secret-pat')

            same_host = handler.redirect_request(
                req, None, 302, 'Found', {}, 'https://api.github.com/repositories/1/releases/latest',
            )
            self.assertEqual(same_host.get_header('Authorization'), 'Bearer secret-pat')

            cross_host = handler.redirect_request(
                req, None, 302, 'Found', {}, 'https://objects.githubusercontent.com/release/asset.tar.gz',
            )
            self.assertIsNone(cross_host.get_header('Authorization'))
            self.assertNotIn('authorization', {k.lower() for k in cross_host.headers})
            self.assertNotIn('authorization', {k.lower() for k in cross_host.unredirected_hdrs})

            non_github = check_pins._request('https://example.com/latest')
            self.assertIsNone(non_github.get_header('Authorization'))
            spoofed = check_pins._request('https://api.github.com.evil.example/releases')
            self.assertIsNone(spoofed.get_header('Authorization'))

    def test_fetch_and_hash_sanitizes_query_and_traversal_filenames(self):
        _, p1 = check_pins.fetch_and_hash(
            {'url': 'https://example.com/releases/app.deb?token=secret&dl=1'},
            self.root,
            self.fake_fetch,
        )
        self.assertEqual(p1, self.root / 'app.deb')
        _, p2 = check_pins.fetch_and_hash(
            {'url': 'https://example.com/..'},
            self.root,
            self.fake_fetch,
        )
        self.assertEqual(p2, self.root / 'download')

    def test_prerelease_pin_is_not_downgraded_to_older_stable_release(self):
        downloads_ail = DOWNLOADS + (
            '  appimagelauncher:\n'
            '    version: "v3.0.0-beta-3"\n'
            '    deb_version: "3.0.0-beta-2"\n'
            '    url: "https://example.com/v3.0.0-beta-3/ail_amd64.deb"\n'
            '    sha256: "dddd"\n'
        )
        (self.root / check_pins.DOWNLOADS).write_text(downloads_ail)
        ail_pin = self.pin('appimagelauncher')

        rows = check_pins.report(
            self.root, [ail_pin],
            lambda pin, current: {'version': 'v2.2.0', 'url': 'https://example.com/v2.2.0/ail_amd64.deb'},
            out=io.StringIO(),
        )
        self.assertTrue(rows[0][3].startswith('up to date'))

        out = io.StringIO()
        failures = check_pins.bump_named(
            self.root, ['appimagelauncher'],
            lambda pin, current: {'version': 'v2.2.0', 'url': 'https://example.com/v2.2.0/ail_amd64.deb'},
            self.fake_fetch, out,
        )
        self.assertEqual(failures, 0)
        self.assertIn('skipping downgrade', out.getvalue())
        self.assertEqual((self.root / check_pins.DOWNLOADS).read_text(), downloads_ail)

        with mock.patch.object(check_pins, 'deb_version', return_value='3.0.0'):
            upgrade_failures = check_pins.bump_named(
                self.root, ['appimagelauncher'],
                lambda pin, current: {'version': 'v3.0.0', 'url': 'https://example.com/v3.0.0/ail_amd64.deb'},
                self.fake_fetch, io.StringIO(),
            )
        self.assertEqual(upgrade_failures, 0)
        entry = yaml.safe_load((self.root / check_pins.DOWNLOADS).read_text())['pinned_downloads']['appimagelauncher']
        self.assertEqual(entry['version'], 'v3.0.0')

    def test_editing_real_files_with_current_values_is_a_no_op(self):
        root = self.root / 'real'
        for relative in (check_pins.DOWNLOADS, check_pins.DEFAULTS, check_pins.BOOTSTRAP):
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(REPO / relative, root / relative)
        downloads = (root / check_pins.DOWNLOADS).read_text()
        for name, entry in yaml.safe_load(downloads)['pinned_downloads'].items():
            downloads = check_pins.set_download_fields(downloads, name, {k: v for k, v in entry.items() if isinstance(v, str)})
        self.assertEqual(downloads, (REPO / check_pins.DOWNLOADS).read_text())

    def test_lookup_antigravity_parses_manifest_and_requires_fields(self):
        manifest = {
            'version': ' 1.5.16 ',
            'url': 'https://example.com/antigravity/1.5.16/cli_linux_x64.tar.gz',
            'sha512': 'ABCDEF' + '0' * 122,
        }
        with mock.patch.object(check_pins, 'http_json', return_value=manifest):
            result = check_pins.lookup_antigravity()
        self.assertEqual(result, {
            'version': '1.5.16',
            'url': 'https://example.com/antigravity/1.5.16/cli_linux_x64.tar.gz',
            'digest': 'abcdef' + '0' * 122,
            'digest_algo': 'sha512',
        })

        for missing_key in ('version', 'url', 'sha512'):
            bad = dict(manifest)
            bad[missing_key] = ''
            with self.subTest(missing_key=missing_key):
                with mock.patch.object(check_pins, 'http_json', return_value=bad):
                    with self.assertRaises(RuntimeError):
                        check_pins.lookup_antigravity()

    def test_bump_antigravity_cli_verifies_sha512_digest(self):
        downloads_with_agy = DOWNLOADS + (
            '  antigravity_cli:\n'
            '    version: "1.3.5"\n'
            '    url: "https://example.com/1.3.5/cli_linux_x64.tar.gz"\n'
            '    sha256: "cccc"\n'
        )
        (self.root / check_pins.DOWNLOADS).write_text(downloads_with_agy)
        sha512 = hashlib.sha512(self.payload).hexdigest()

        bad_info = {
            'version': '1.5.16',
            'url': 'https://example.com/1.5.16/cli_linux_x64.tar.gz',
            'digest': '0' * 128,
            'digest_algo': 'sha512',
        }
        out = io.StringIO()
        failures = check_pins.bump_named(
            self.root, ['antigravity_cli'], lambda pin, current: bad_info, self.fake_fetch, out,
        )
        self.assertEqual(failures, 1)
        self.assertIn('SHA-512 mismatch', out.getvalue())
        self.assertEqual((self.root / check_pins.DOWNLOADS).read_text(), downloads_with_agy)

        good_info = dict(bad_info, digest=sha512)
        out = io.StringIO()
        failures = check_pins.bump_named(
            self.root, ['antigravity_cli'], lambda pin, current: good_info, self.fake_fetch, out,
        )
        self.assertEqual(failures, 0, out.getvalue())
        entry = yaml.safe_load((self.root / check_pins.DOWNLOADS).read_text())['pinned_downloads']['antigravity_cli']
        self.assertEqual(entry, {
            'version': '1.5.16',
            'url': good_info['url'],
            'sha256': self.sha,
        })

    def test_rejects_shell_and_yaml_injection_in_versions_and_urls(self):
        bad_versions = [
            '1.0.0"\nPWNED="1',
            '1.0.0$(touch /tmp/pwn)',
            '1.0.0`id`',
            '1.0.0";evil: true',
            '1.0.0\\nfoo',
            '-bad-leading-dash',
        ]
        for bad in bad_versions:
            with self.subTest(bad_version=bad):
                with self.assertRaises(ValueError):
                    check_pins.set_shell_var(BOOTSTRAP, 'UV_VERSION', bad)
                with self.assertRaises(ValueError):
                    check_pins.set_download_fields(DOWNLOADS, 'arduino_ide', {'version': bad})
                with self.assertRaises(ValueError):
                    check_pins.set_default(DEFAULTS, 'pynvim_version', bad)
                with self.assertRaises(ValueError):
                    check_pins.set_collection_version(REQUIREMENTS, 'ansible.posix', bad)
                out = io.StringIO()
                self.assertEqual(
                    check_pins.bump_named(
                        self.root, ['pynvim'],
                        lambda pin, current: {'version': bad},
                        self.fake_fetch, out,
                    ),
                    1,
                )

        bad_urls = [
            'https://example.com/app.zip"$(id)"',
            'https://example.com/app.zip\nurl2: evil',
            'https://example.com/app.zip`id`',
            'http://example.com/app.zip',
            'https://example.com/with space.zip',
        ]
        for bad_url in bad_urls:
            with self.subTest(bad_url=bad_url):
                with self.assertRaises(ValueError):
                    check_pins.set_shell_var(BOOTSTRAP, 'UV_URL', bad_url)
                with self.assertRaises(ValueError):
                    check_pins.set_download_fields(DOWNLOADS, 'arduino_ide', {'url': bad_url})
                out = io.StringIO()
                self.assertEqual(
                    check_pins.bump_named(
                        self.root, ['uv'],
                        lambda pin, current: {'version': '0.14.0', 'url': bad_url},
                        self.fake_fetch, out,
                    ),
                    1,
                )

        with self.assertRaises(ValueError):
            check_pins.set_shell_var(BOOTSTRAP, 'UV_SHA256', 'not-a-sha256')
        self.assertEqual((self.root / check_pins.BOOTSTRAP).read_text(), BOOTSTRAP)
        self.assertEqual((self.root / check_pins.DOWNLOADS).read_text(), DOWNLOADS)
        self.assertEqual((self.root / check_pins.DEFAULTS).read_text(), DEFAULTS)


if __name__ == '__main__':
    unittest.main()
