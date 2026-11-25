"""Unit tests for scripts/update.sh using stub commands on an isolated PATH."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
UPDATE_SH = REPO / 'scripts/update.sh'
BASH = shutil.which('bash')


class UpdateScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pop-update-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.log = self.root / 'calls.log'
        # Only these real tools are visible; everything else must be stubbed.
        for tool in ('timeout', 'git', 'cat'):
            real = shutil.which(tool)
            if real:
                (self.bin / tool).symlink_to(real)

    def stub(self, name, body='exit 0'):
        path = self.bin / name
        path.write_text(f'#!{BASH}\nprintf "%s\\n" "{name} $*" >> "{self.log}"\n{body}\n')
        path.chmod(0o755)

    def run_update(self, *arguments):
        env = {'HOME': str(self.home), 'PATH': str(self.bin), 'LC_ALL': 'C'}
        return subprocess.run([BASH, str(UPDATE_SH), *arguments], env=env,
                              capture_output=True, text=True, timeout=60)

    def calls(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_apt_runs_full_upgrade_through_sudo_and_asks_by_default(self):
        self.stub('sudo', 'exec "$@"')
        self.stub('apt-get')
        result = self.run_update('--only', 'apt')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('apt-get update', self.calls())
        self.assertIn('apt-get full-upgrade', self.calls())
        self.assertNotIn('apt-get full-upgrade -y', self.calls())

    def test_yes_passes_assume_yes_to_apt(self):
        self.stub('sudo', 'exec "$@"')
        self.stub('apt-get')
        result = self.run_update('--only', 'apt', '--yes')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('apt-get full-upgrade -y', self.calls())

    def test_missing_tools_are_skipped_not_failed(self):
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stderr)
        for step in ('apt', 'flatpak', 'firmware', 'nvim', 'tmux', 'mise'):
            self.assertIn(f'{step}: skipped (not installed or not configured)', result.stdout)

    def test_failed_step_is_reported_and_later_steps_still_run(self):
        self.stub('flatpak', 'exit 1')
        self.stub('mise', 'exit 0')
        result = self.run_update('--only', 'flatpak,mise')
        self.assertEqual(result.returncode, 1)
        self.assertIn('flatpak: FAILED', result.stdout)
        self.assertIn('mise: ok', result.stdout)
        self.assertIn('mise outdated --bump', self.calls())

    def test_firmware_exit_code_2_means_no_updates(self):
        self.stub('fwupdmgr', '[[ $1 == get-updates ]] && exit 2; exit 0')
        result = self.run_update('--only', 'firmware')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('No firmware updates.', result.stdout)
        self.assertNotIn('fwupdmgr update', self.calls())

    def test_skip_and_unknown_steps(self):
        self.stub('flatpak')
        result = self.run_update('--only', 'flatpak,mise', '--skip', 'flatpak')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('flatpak update -y', self.calls())
        bad = self.run_update('--skip', 'bogus')
        self.assertEqual(bad.returncode, 2)
        self.assertIn('Unknown step: bogus', bad.stderr)

    def test_tmux_updates_plugins_with_tpm(self):
        tpm = self.home / '.local/share/tmux/plugins/tpm/bin'
        tpm.mkdir(parents=True)
        script = tpm / 'update_plugins'
        script.write_text(f'#!{BASH}\nprintf "%s\\n" "tpm $* $TMUX_PLUGIN_MANAGER_PATH" >> "{self.log}"\n')
        script.chmod(0o755)
        result = self.run_update('--only', 'tmux')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f'tpm all {self.home}/.local/share/tmux/plugins', self.calls())

    def test_bump_mise_readds_only_chezmoi_managed_files(self):
        config = self.home / '.config/mise'
        config.mkdir(parents=True)
        (config / 'config.toml').write_text('[tools]\n')
        (config / 'mise.lock').write_text('')
        self.stub('mise', '[[ $1 == outdated ]] && echo "node 24 26"; exit 0')
        # Chezmoi manages config.toml but not mise.lock.
        self.stub('chezmoi', '[[ $1 == source-path && $2 == *mise.lock ]] && exit 1; exit 0')
        result = self.run_update('--only', 'mise', '--bump-mise')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('mise upgrade --bump', self.calls())
        self.assertIn(f'chezmoi re-add {config}/config.toml', self.calls())
        self.assertFalse(any('re-add' in c and 'mise.lock' in c for c in self.calls()))
        self.assertFalse(any(c.startswith('chezmoi add') for c in self.calls()))

    def test_bump_mise_adds_new_per_package_lockfiles(self):
        config = self.home / '.config/mise'
        (config / 'locks/npm-openai-codex/0.172.0').mkdir(parents=True)
        (config / 'config.toml').write_text('[tools]\n')
        (config / 'mise.lock').write_text('')
        self.stub('mise', '[[ $1 == outdated ]] && echo "npm:@openai/codex 0.171.0 0.172.0"; exit 0')
        self.stub('chezmoi')  # Manages config.toml and mise.lock.
        result = self.run_update('--only', 'mise', '--bump-mise')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f'chezmoi re-add {config}/config.toml {config}/mise.lock', self.calls())
        self.assertIn(f'chezmoi add {config}/locks', self.calls())

    def test_report_without_bump_does_not_upgrade(self):
        self.stub('mise', '[[ $1 == outdated ]] && echo "node 24 26"; exit 0')
        result = self.run_update('--only', 'mise')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('node 24 26', result.stdout)
        self.assertNotIn('mise upgrade --bump', self.calls())

    def test_mise_outdated_failure_surfaces_stderr(self):
        self.stub('mise', 'printf "mise rate limit error\\n" >&2; exit 1')
        result = self.run_update('--only', 'mise')
        self.assertEqual(result.returncode, 1)
        self.assertIn('mise: FAILED', result.stdout)
        self.assertIn('mise rate limit error', result.stderr)


if __name__ == '__main__':
    unittest.main()
