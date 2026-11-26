"""Tests for v2 bootstrap, Ansible foundation, and pinned download metadata."""
import os
from pathlib import Path
import re
import shlex
import shutil
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

    def test_bootstrap_download_verified_preserves_existing_file_on_failure(self):
        dest = self.root / 'tool.bin'
        dest.write_text('working')
        bad_hash = self.bash('''
curl() { printf partial > "${@: -1}"; }
download_verified https://example.invalid/tool.bin "$1" "$(printf '%064d' 0)"
''', dest)
        self.assertNotEqual(bad_hash.returncode, 0)
        self.assertEqual(dest.read_text(), 'working')
        self.assertFalse(list(self.root.glob('*.download.*')))

        net_err = self.bash(
            'curl() { return 42; }; download_verified https://example.invalid/tool.bin "$1" ignored',
            dest,
        )
        self.assertNotEqual(net_err.returncode, 0)
        self.assertEqual(dest.read_text(), 'working')

    def test_bootstrap_install_ansible_enforces_exact_pins_when_version_differs(self):
        calls = self.root / 'calls.log'
        result = self.bash('''
SCRIPT_DIR="$2"
log_file="$1"
ansible-playbook() { printf 'ansible-playbook [core 2.19.0]\n'; }
ansible-lint() { printf 'ansible-lint 25.1.0\n'; }
uv() { printf 'uv %s\n' "$*" >> "$log_file"; }
ansible-galaxy() { printf 'galaxy %s\n' "$*" >> "$log_file"; }
install_ansible
''', calls, REPO)
        self.assertEqual(result.returncode, 0, result.stderr)
        logged = calls.read_text()
        self.assertIn('uv tool install --force --with-executables-from ansible-core ansible-core==', logged)
        self.assertIn('uv tool install --force ansible-lint==', logged)

    def test_bootstrap_install_mise_upgrades_outdated_local_mise(self):
        home = self.root / 'home'
        local_bin = home / '.local/bin'
        local_bin.mkdir(parents=True)
        mise_path = local_bin / 'mise'
        mise_path.write_text('#!/bin/sh\nprintf "2026.1.0 linux-x64 (2026-01-01)\\n"\n')
        mise_path.chmod(0o755)
        new_payload = b'#!/bin/sh\nprintf "upgraded-mise\\n"\n'
        import hashlib
        new_sha = hashlib.sha256(new_payload).hexdigest()
        payload_file = self.root / 'new-mise'
        payload_file.write_bytes(new_payload)
        res = self.bash('''
HOME="$1"
WORK_DIR="$2"
MISE_SHA256="$3"
PAYLOAD="$4"
PATH="$HOME/.local/bin:$PATH"
curl() { cp "$PAYLOAD" "${@: -1}"; }
install_mise
''', home, self.root / 'work', new_sha, payload_file)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(mise_path.read_bytes(), new_payload)

        skip_res = self.bash('''
HOME="$1"
WORK_DIR="$2"
PATH="$HOME/.local/bin:$PATH"
mise() { printf "%s linux-x64\\n" "${MISE_VERSION#v}"; }
curl() { printf 'should not download\n' >&2; return 99; }
install_mise
''', home, self.root / 'work')
        self.assertEqual(skip_res.returncode, 0, skip_res.stderr)

    def test_tpm_missing_plugin_probe_ignores_comments_and_strips_branch_refs(self):
        tasks = yaml.safe_load((ANSIBLE_DIR / 'roles/workstation/tasks/main.yml').read_text(encoding='utf-8'))
        tmux_block = next(entry['block'] for entry in tasks if 'tmux' in entry.get('tags', []))
        probe_task = next(t for t in tmux_block if t['name'] == 'Check declared tmux plugins missing from plugin directory')
        argv = probe_task['ansible.builtin.command']['argv']
        self.assertEqual(argv[:2], ['python3', '{{ repo_root }}/scripts/tmux_missing_plugins.py'])

        home = self.root / 'home'
        pdir = home / '.local/share/tmux/plugins'
        (home / '.config/tmux').mkdir(parents=True)
        (pdir / 'installed-plugin').mkdir(parents=True)
        (home / '.config/tmux/tmux.conf').write_text(
            "# set -g @plugin 'commented/ignored'\n"
            "  set -g @plugin 'tmux-plugins/tpm'\n"
            "set -g @plugin 'user/branch-plugin#main'\n"
            "set -g @plugin 'https://github.com/user/git-plugin.git'\n"
            "set-option -g @plugin \"user/installed-plugin#v2\" # inline comment\n"
        )
        res = subprocess.run(
            ['python3', str(REPO / 'scripts/tmux_missing_plugins.py'), str(home)],
            capture_output=True, text=True, check=True,
        )
        self.assertEqual(res.stdout.splitlines(), ['tpm', 'branch-plugin', 'git-plugin'])

    def test_desktop_apps_and_systemd_check_mode_guards(self):
        apt_apps = yaml.safe_load((ANSIBLE_DIR / 'roles/desktop_apps/tasks/apt_apps.yml').read_text(encoding='utf-8'))
        kicad_block = next(entry['block'] for entry in apt_apps if 'block' in entry and 'kicad' in entry.get('tags', []))
        uv_probe = next(t for t in kicad_block if t['name'] == 'Check whether uv is available for kicad-python venv')
        self.assertIs(uv_probe['check_mode'], False)
        kicad_key_verify = next(t for t in kicad_block if t['name'] == 'Verify installed KiCad PPA signing key fingerprint')
        self.assertIs(kicad_key_verify['check_mode'], False)
        self.assertIn('pgp-verify', kicad_key_verify['ansible.builtin.command']['argv'])
        kicad_key_dl = next(t for t in kicad_block if t['name'] == 'Download and verify KiCad PPA signing key')
        self.assertIn('not ansible_check_mode', kicad_key_dl['when'])

        vendor_apps = yaml.safe_load((ANSIBLE_DIR / 'roles/desktop_apps/tasks/vendor_apps.yml').read_text(encoding='utf-8'))
        saleae_block = next(entry['block'] for entry in vendor_apps if 'saleae' in entry.get('tags', []))
        saleae_dir_stat = next(t for t in saleae_block if t['name'] == 'Check whether Saleae Logic destination directory exists')
        self.assertEqual(saleae_dir_stat['register'], 'desktop_apps_saleae_dir_stat')
        saleae_dl = next(t for t in saleae_block if t['name'] == 'Download verified Saleae Logic 2 AppImage')
        self.assertIn('desktop_apps_saleae_dir_stat.stat.exists', saleae_dl['when'])

        flatpak_tasks = yaml.safe_load((ANSIBLE_DIR / 'roles/desktop_apps/tasks/flatpak.yml').read_text(encoding='utf-8'))
        fp_probe = next(t for t in flatpak_tasks if t['name'] == 'Check whether flatpak binary is installed')
        self.assertIs(fp_probe['check_mode'], False)
        for task_name in ('Configure user Flathub remote', 'Install Flathub desktop applications'):
            task = next(t for t in flatpak_tasks if t['name'] == task_name)
            self.assertIn('desktop_apps_flatpak_which.rc == 0', task['when'])

        system_tasks = yaml.safe_load((ANSIBLE_DIR / 'roles/system/tasks/main.yml').read_text(encoding='utf-8'))
        ufw_probe = next(t for t in system_tasks if t['name'] == 'Check whether UFW binary is installed')
        oomd_probe = next(t for t in system_tasks if t['name'] == 'Check whether systemd-oomd.service unit exists')
        self.assertIs(ufw_probe['check_mode'], False)
        self.assertIs(oomd_probe['check_mode'], False)

        snapper_tasks = yaml.safe_load((ANSIBLE_DIR / 'roles/snapper/tasks/main.yml').read_text(encoding='utf-8'))
        cleanup_probe = next(t for t in snapper_tasks if t['name'] == 'Check whether snapper-cleanup.timer unit exists')
        self.assertIs(cleanup_probe['check_mode'], False)

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

        text = BOOTSTRAP.read_text(encoding='utf-8')
        self.assertRegex(text, r'ANSIBLE_CORE_VERSION="[0-9]+\.[0-9]+\.[0-9]+"')
        self.assertRegex(text, r'ANSIBLE_LINT_VERSION="[0-9]+\.[0-9]+\.[0-9]+"')
        self.assertNotIn('self_update', text)

    def test_requirements_versions_are_pinned_exactly(self):
        reqs = yaml.safe_load((ANSIBLE_DIR / 'requirements.yml').read_text(encoding='utf-8'))
        for entry in reqs['collections']:
            with self.subTest(name=entry['name']):
                self.assertRegex(entry['version'], r'^[0-9]+\.[0-9]+\.[0-9]+$')

    def test_ansible_run_dispatches_positional_args_and_mise_tasks(self):
        stub_dir = self.root / 'bin'
        stub_dir.mkdir()
        stub = stub_dir / 'ansible-playbook'
        stub.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
        stub.chmod(0o755)
        env = dict(os.environ, PATH=f'{stub_dir}:{os.environ.get("PATH", "")}')

        missing_tag = subprocess.run(
            ['bash', str(REPO / 'scripts/ansible_run.sh'), 'role'],
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(missing_tag.returncode, 2)
        self.assertIn('Usage: mise run role', missing_tag.stderr)

        role_res = subprocess.run(
            ['bash', str(REPO / 'scripts/ansible_run.sh'), 'role', 'stm32', '--check'],
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(role_res.returncode, 0, role_res.stderr)
        lines = role_res.stdout.splitlines()
        self.assertIn('--tags', lines)
        idx = lines.index('--tags')
        self.assertEqual(lines[idx + 1], 'stm32')
        self.assertIn('--check', lines)

        mise_bin = subprocess.run(['which', 'mise'], capture_output=True, text=True)
        if mise_bin.returncode == 0:
            mise_env = dict(env, MISE_TRUSTED_CONFIG_PATHS=str(REPO))
            mise_role = subprocess.run(
                [mise_bin.stdout.strip(), 'run', 'role', 'stm32', '--check'],
                cwd=REPO,
                env=mise_env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(mise_role.returncode, 0, mise_role.stderr)
            mise_lines = mise_role.stdout.splitlines()
            self.assertIn('--tags', mise_lines)
            self.assertEqual(mise_lines[mise_lines.index('--tags') + 1], 'stm32')
            self.assertIn('--check', mise_lines)

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
            'cosmic_swipe',
            'vscode_key',
            'mozilla_key',
            'githubcli_key',
            'claude_code_key',
            'kicad_key',
            'google_chrome_key',
            'llvm_key',
        }
        self.assertEqual(set(v2_downloads.keys()), expected_keys)
        url_version_keys = {
            'font',
            'appimagelauncher',
            'saleae',
            'synology',
            'hyper_backup_explorer',
            'antigravity_cli',
            'arduino_ide',
            'cosmic_swipe',
        }
        deb_version_keys = {'synology', 'appimagelauncher'}
        fingerprint_key_keys = {'kicad_key'}
        for key, entry in v2_downloads.items():
            with self.subTest(key=key):
                self.assertTrue(entry['version'])
                self.assertTrue(entry['url'].startswith('https://'))
                self.assertNotIn('/latest/', entry['url'])
                if key in fingerprint_key_keys:
                    self.assertRegex(entry['fingerprint'], re.compile(r'^[0-9A-F]{40}$'))
                    self.assertIn(entry['fingerprint'], entry['url'])
                    self.assertNotIn('sha256', entry)
                else:
                    self.assertRegex(entry['sha256'], re.compile(r'^[0-9a-f]{64}$'))
                if key in url_version_keys:
                    self.assertIn(entry['version'], entry['url'])
                if key in deb_version_keys:
                    self.assertTrue(entry.get('deb_version'))

    def test_group_vars_local_yml_sorts_last(self):
        group_vars_dir = ANSIBLE_DIR / 'group_vars/all'
        for path in sorted(group_vars_dir.iterdir()):
            if path.is_file() and path.suffix in {'.yml', '.yaml', '.json'} and path.name != 'local.yml':
                self.assertLess(
                    path.name,
                    'local.yml',
                    f'{path.relative_to(REPO)} must sort before local.yml so local overrides win',
                )

    def test_e2e_container_test_cli_guards_and_inner_shell(self):
        script = REPO / 'scripts/e2e_container_test.sh'
        self.assertTrue(script.is_file(), f'missing {script}')
        self.assertTrue(os.access(script, os.X_OK), f'{script} must be executable')

        help_res = subprocess.run(
            ['bash', str(script), '--help'],
            cwd=REPO,
            capture_output=True,
            text=True,
        )
        self.assertEqual(help_res.returncode, 0, help_res.stderr)
        self.assertIn('podman run', help_res.stdout)
        self.assertIn('scripts/e2e_container_test.sh', help_res.stdout)

        bad_opt = subprocess.run(
            ['bash', str(script), '--unknown-flag'],
            cwd=REPO,
            capture_output=True,
            text=True,
        )
        self.assertEqual(bad_opt.returncode, 2, bad_opt.stderr)
        self.assertIn('Unknown option', bad_opt.stderr)

        in_container = Path('/.dockerenv').exists() or Path('/run/.containerenv').exists()
        if not in_container:
            guard = subprocess.run(
                ['bash', str(script)],
                cwd=REPO,
                capture_output=True,
                text=True,
            )
            self.assertEqual(guard.returncode, 1, guard.stderr)
            self.assertIn('Refusing to run outside a disposable container', guard.stderr)
        elif os.geteuid() != 0:
            guard = subprocess.run(
                ['bash', str(script)],
                cwd=REPO,
                capture_output=True,
                text=True,
            )
            self.assertEqual(guard.returncode, 1, guard.stderr)
            self.assertIn('must start as root inside the container', guard.stderr)

        # Verify the dynamic version extraction against bootstrap.sh matches semver.
        awk_res = subprocess.run(
            [
                'bash',
                '-euo',
                'pipefail',
                '-c',
                'core=$(awk -F\'"\' \'$1 == "ANSIBLE_CORE_VERSION=" { print $2; exit }\' ./bootstrap.sh); '
                'lint=$(awk -F\'"\' \'$1 == "ANSIBLE_LINT_VERSION=" { print $2; exit }\' ./bootstrap.sh); '
                'printf "%s\\n%s\\n" "$core" "$lint"',
            ],
            cwd=REPO,
            capture_output=True,
            text=True,
        )
        self.assertEqual(awk_res.returncode, 0, awk_res.stderr)
        lines = awk_res.stdout.strip().splitlines()
        self.assertEqual(len(lines), 2, awk_res.stdout)
        for ver in lines:
            self.assertRegex(ver, r'^\d+\.\d+\.\d+$')

        # Validate the inner su heredoc body with bash -n and shellcheck.
        text = script.read_text()
        marker = "su - tester -s /bin/bash <<'EOF'\n"
        self.assertIn(marker, text)
        inner_and_tail = text.split(marker, 1)[1]
        self.assertTrue(inner_and_tail.endswith('\nEOF\n'))
        inner = inner_and_tail[:-len('\nEOF\n')]

        bash_n = subprocess.run(
            ['bash', '-n'],
            input=inner,
            capture_output=True,
            text=True,
        )
        self.assertEqual(bash_n.returncode, 0, bash_n.stderr)

        if shutil.which('shellcheck'):
            sc = subprocess.run(
                ['shellcheck', '-s', 'bash', '-'],
                input=inner,
                capture_output=True,
                text=True,
            )
            self.assertEqual(sc.returncode, 0, sc.stdout + sc.stderr)

    def test_standalone_subtag_prerequisites_and_apt_cache_updates(self):
        desktop_main = yaml.safe_load((ANSIBLE_DIR / 'roles/desktop_apps/tasks/main.yml').read_text())
        dirs_task = next(t for t in desktop_main if t['name'] == 'Ensure workstation user directories exist')
        for tag in ('fonts', 'vscode', 'kicad', 'flatpak', 'arduino', 'saleae',
                    'synology', 'hyper_backup', 'appimagelauncher'):
            self.assertIn(tag, dirs_task['tags'])

        apt_apps = yaml.safe_load((ANSIBLE_DIR / 'roles/desktop_apps/tasks/apt_apps.yml').read_text())
        keyrings_task = next(t for t in apt_apps if t['name'] == 'Ensure APT keyrings directory exists')
        self.assertEqual(set(keyrings_task['tags']), {'vscode', 'kicad'})

        ai_agents = yaml.safe_load((ANSIBLE_DIR / 'roles/ai_agents/tasks/main.yml').read_text())
        bin_task = next(t for t in ai_agents if t['name'] == 'Ensure ~/.local/bin directory exists')
        self.assertIn('antigravity', bin_task['tags'])

        for rel_path in ('roles/toolchains/tasks/llvm.yml', 'roles/browsers/tasks/chrome.yml'):
            tasks = yaml.safe_load((ANSIBLE_DIR / rel_path).read_text())
            self.assertTrue(
                any(
                    t.get('ansible.builtin.file', {}).get('path') == '/etc/apt/keyrings'
                    and t['ansible.builtin.file'].get('state') == 'directory'
                    for t in tasks
                ),
                f'{rel_path} must ensure /etc/apt/keyrings exists',
            )

        apt_targets = [
            ('roles/toolchains/tasks/gcc.yml', 'Install native build tools and newest GCC/G++ major'),
            ('roles/toolchains/tasks/embedded.yml', 'Install ARM bare-metal and AArch64 cross-compilers'),
            ('roles/toolchains/tasks/embedded.yml', 'Install AVR cross-compilers and flashing tools'),
            ('roles/toolchains/tasks/embedded.yml', 'Install RISC-V cross-compilers and QEMU'),
            ('roles/containers/tasks/main.yml', 'Install rootless Podman packages'),
            ('roles/desktop_apps/tasks/apt_apps.yml', 'Install desktop utility and CAD APT packages'),
        ]
        for rel_path, task_name in apt_targets:
            tasks = yaml.safe_load((ANSIBLE_DIR / rel_path).read_text())
            task = next(t for t in tasks if t['name'] == task_name)
            self.assertIs(task['ansible.builtin.apt']['update_cache'], True, task_name)
            self.assertEqual(task['ansible.builtin.apt']['cache_valid_time'], 3600, task_name)

        dropbox_block = next(t for t in apt_apps if t['name'] == 'Install optional nautilus-dropbox integration when enabled')['block']
        dropbox_apt = next(t for t in dropbox_block if t['name'] == 'Install nautilus-dropbox when enabled')['ansible.builtin.apt']
        self.assertIs(dropbox_apt['update_cache'], True)
        self.assertEqual(dropbox_apt['cache_valid_time'], 3600)

        vendor_apps = yaml.safe_load((ANSIBLE_DIR / 'roles/desktop_apps/tasks/vendor_apps.yml').read_text())
        hbe_block = next(t for t in vendor_apps if t['name'] == 'Install Synology Hyper Backup Explorer')['block']
        hbe_apt = next(t for t in hbe_block if t['name'] == 'Install Hyper Backup Explorer runtime dependencies')['ansible.builtin.apt']
        self.assertIs(hbe_apt['update_cache'], True)
        self.assertEqual(hbe_apt['cache_valid_time'], 3600)
