"""Tests for v2 bootstrap, Ansible foundation, and pinned download metadata."""
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import unittest
import yaml

REPO = Path(__file__).resolve().parents[1]
BOOTSTRAP = REPO / 'bootstrap.sh'
ANSIBLE_DIR = REPO / 'ansible'


class BootstrapAndV2FoundationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pop-v2-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def bash(self, code, *arguments, env=None):
        environment = dict(os.environ, TMPDIR=str(self.root))
        if env:
            environment.update(env)
        return subprocess.run(
            ['bash', '-euo', 'pipefail', '-c',
             f'source {shlex.quote(str(BOOTSTRAP))}\n{code}',
             'test', *map(str, arguments)],
            cwd=self.root,
            env=environment,
            capture_output=True,
            text=True,
        )

    def write_os_release(self, os_id, version_id):
        path = self.root / 'os-release'
        path.write_text(f'ID="{os_id}"\nVERSION_ID="{version_id}"\n')
        return path

    def test_bootstrap_rejects_root_user(self):
        result = self.bash('require_user 0')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('without sudo', result.stderr)

    def test_bootstrap_accepts_non_root_user(self):
        result = self.bash('require_user 1000')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_bootstrap_accepts_supported_24_plus_systems(self):
        for os_id, version_id in (('pop', '24.04'), ('ubuntu', '24.04'), ('pop', '26.04')):
            with self.subTest(os_id=os_id, version_id=version_id):
                os_release = self.write_os_release(os_id, version_id)
                result = self.bash('require_supported_system "$1" x86_64', os_release)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_bootstrap_rejects_legacy_or_unsupported_systems(self):
        cases = (
            ('pop', '22.04', 'x86_64'),
            ('ubuntu', '22.04', 'x86_64'),
            ('debian', '12', 'x86_64'),
            ('pop', 'rolling', 'x86_64'),
            ('pop', '24.04', 'aarch64'),
        )
        for os_id, version_id, arch in cases:
            with self.subTest(os_id=os_id, version_id=version_id, arch=arch):
                os_release = self.write_os_release(os_id, version_id)
                result = self.bash('require_supported_system "$1" "$2"', os_release, arch)
                self.assertNotEqual(result.returncode, 0)

    def test_bootstrap_verify_sha256(self):
        sample = self.root / 'sample.bin'
        sample.write_bytes(b'pop-v2-checksum-test')
        good = self.bash('verify_sha256 "$1" "$(sha256sum "$1" | cut -d" " -f1)"', sample)
        self.assertEqual(good.returncode, 0, good.stderr)

        bad_digest = self.bash('verify_sha256 "$1" "0000"', sample)
        self.assertNotEqual(bad_digest.returncode, 0)

        mismatch = self.bash('verify_sha256 "$1" "' + ('0' * 64) + '"', sample)
        self.assertNotEqual(mismatch.returncode, 0)

    def test_bootstrap_pins_are_valid_https_and_sha256(self):
        result = self.bash(
            'printf "%s\\n%s\\n%s\\n%s\\n%s\\n%s\\n" '
            '"$MISE_VERSION" "$MISE_URL" "$MISE_SHA256" '
            '"$UV_VERSION" "$UV_URL" "$UV_SHA256"'
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        mise_ver, mise_url, mise_sha, uv_ver, uv_url, uv_sha = result.stdout.splitlines()
        for ver, url, sha in ((mise_ver, mise_url, mise_sha), (uv_ver, uv_url, uv_sha)):
            self.assertTrue(ver)
            self.assertTrue(url.startswith('https://'))
            self.assertNotIn('/latest/', url)
            self.assertRegex(sha, re.compile(r'^[0-9a-f]{64}$'))

    def test_ansible_yaml_files_parse_cleanly(self):
        yaml_files = sorted(ANSIBLE_DIR.rglob('*.yml'))
        self.assertTrue(yaml_files)
        for path in yaml_files:
            with self.subTest(path=path.relative_to(REPO)):
                yaml.safe_load(path.read_text())

    def test_playbook_references_all_v2_roles(self):
        playbook = yaml.safe_load((ANSIBLE_DIR / 'playbook.yml').read_text())
        role_names = [entry['role'] for entry in playbook[0]['roles']]
        self.assertEqual(
            role_names,
            [
                'system',
                'snapper',
                'toolchains',
                'containers',
                'browsers',
                'desktop_apps',
                'ai_agents',
                'workstation',
                'stm32',
            ],
        )
        for role_name in role_names:
            self.assertTrue((ANSIBLE_DIR / 'roles' / role_name / 'tasks/main.yml').is_file())

    def test_ansible_yaml_line_lengths(self):
        for path in sorted(ANSIBLE_DIR.rglob('*.yml')):
            for lineno, line in enumerate(path.read_text(encoding='utf-8').splitlines(), start=1):
                self.assertLessEqual(
                    len(line),
                    160,
                    f'{path.relative_to(REPO)}:{lineno} exceeds 160 chars ({len(line)})',
                )

    def test_ansible_notifies_match_role_handlers(self):
        def collect_notifies(node):
            found = []
            if isinstance(node, dict):
                if 'notify' in node:
                    val = node['notify']
                    if isinstance(val, str):
                        found.append(val)
                    elif isinstance(val, list):
                        found.extend(item for item in val if isinstance(item, str))
                for value in node.values():
                    found.extend(collect_notifies(value))
            elif isinstance(node, list):
                for item in node:
                    found.extend(collect_notifies(item))
            return found

        for role_dir in sorted((ANSIBLE_DIR / 'roles').iterdir()):
            if not role_dir.is_dir():
                continue
            handlers_file = role_dir / 'handlers/main.yml'
            handlers = set()
            if handlers_file.is_file():
                parsed_handlers = yaml.safe_load(handlers_file.read_text(encoding='utf-8')) or []
                for entry in parsed_handlers:
                    if isinstance(entry, dict):
                        self.assertNotIn(
                            'block',
                            entry,
                            f'{handlers_file.relative_to(REPO)} must not use block in handlers',
                        )
                        if 'name' in entry:
                            handlers.add(entry['name'])
            for task_file in sorted((role_dir / 'tasks').glob('*.yml')):
                parsed_tasks = yaml.safe_load(task_file.read_text(encoding='utf-8')) or []
                for notified in collect_notifies(parsed_tasks):
                    self.assertIn(
                        notified,
                        handlers,
                        f'{task_file.relative_to(REPO)} notifies unknown handler {notified!r}',
                    )

    def test_ansible_downloads_are_complete_and_valid(self):
        v2_downloads = yaml.safe_load((ANSIBLE_DIR / 'group_vars/all/downloads.yml').read_text())['pinned_downloads']
        expected_keys = {
            'font',
            'appimagelauncher',
            'saleae',
            'synology',
            'hyper_backup_explorer',
            'antigravity_cli',
            'arduino_ide',
            'vscode_key',
            'mozilla_key',
            'githubcli_key',
            'claude_code_key',
            'kicad_key',
            'google_chrome_key',
            'llvm_key',
        }
        self.assertEqual(set(v2_downloads.keys()), expected_keys)
        for key, entry in v2_downloads.items():
            with self.subTest(key=key):
                self.assertTrue(entry['version'])
                self.assertTrue(entry['url'].startswith('https://'))
                self.assertNotIn('/latest/', entry['url'])
                self.assertRegex(entry['sha256'], re.compile(r'^[0-9a-f]{64}$'))

    def test_group_vars_local_yml_sorts_last(self):
        group_vars_dir = ANSIBLE_DIR / 'group_vars/all'
        for path in sorted(group_vars_dir.iterdir()):
            if path.is_file() and path.suffix in {'.yml', '.yaml', '.json'} and path.name != 'local.yml':
                self.assertLess(
                    path.name,
                    'local.yml',
                    f'{path.relative_to(REPO)} must sort before local.yml so local overrides win',
                )
