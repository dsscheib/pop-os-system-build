"""Unit tests for scripts/auth.sh."""
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
AUTH_SH = REPO / 'scripts/auth.sh'


class AuthScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pop-auth-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def bash(self, code, *arguments, env=None):
        environment = dict(os.environ, HOME=str(self.root), TMPDIR=str(self.root))
        if env:
            environment.update(env)
        return subprocess.run(
            ['bash', '-euo', 'pipefail', '-c',
             f'source {shlex.quote(str(AUTH_SH))}\n{code}',
             'test', *map(str, arguments)],
            cwd=self.root,
            env=environment,
            capture_output=True,
            text=True,
        )

    def test_help_and_invalid_step(self):
        help_res = subprocess.run(['bash', str(AUTH_SH), '--help'], capture_output=True, text=True)
        self.assertEqual(help_res.returncode, 0)
        self.assertIn('github', help_res.stdout)

        bad_res = subprocess.run(['bash', str(AUTH_SH), '--step', 'bogus'], capture_output=True, text=True)
        self.assertNotEqual(bad_res.returncode, 0)
        self.assertIn('Unknown step', bad_res.stderr)

    def test_gdrive_oauth_env_rejects_loose_permissions(self):
        oauth = self.root / 'gdrive-oauth.env'
        oauth.write_text('GDRIVE_CLIENT_ID=id\nGDRIVE_CLIENT_SECRET=secret\n')
        oauth.chmod(0o644)
        res = self.bash('load_gdrive_oauth_env', env={'GDRIVE_OAUTH_ENV': str(oauth)})
        self.assertNotEqual(res.returncode, 0)
        self.assertIn('chmod 600', res.stderr)

        oauth.chmod(0o600)
        ok = self.bash(
            'load_gdrive_oauth_env && printf "%s:%s" "$GDRIVE_CLIENT_ID" "$GDRIVE_CLIENT_SECRET"',
            env={'GDRIVE_OAUTH_ENV': str(oauth)},
        )
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertEqual(ok.stdout, 'id:secret')

    def test_gdrive_baseline_id_tracks_filters_hash(self):
        filters = self.root / 'filters.txt'
        filters.write_text('+ Docs/**\n- **\n')
        r1 = self.bash('gdrive_baseline_id', env={'GDRIVE_FILTERS_FILE': str(filters)})
        self.assertEqual(r1.returncode, 0, r1.stderr)

        filters.write_text('+ Code/**\n- **\n')
        r2 = self.bash('gdrive_baseline_id', env={'GDRIVE_FILTERS_FILE': str(filters)})
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertNotEqual(r1.stdout, r2.stdout)

    def test_auth_github_skips_when_already_authenticated(self):
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        gh = bin_dir / 'gh'
        gh.write_text('#!/usr/bin/env bash\nexit 0\n')
        gh.chmod(0o755)
        res = self.bash('auth_github', env={'PATH': f'{bin_dir}:{os.environ["PATH"]}'})
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn('already authenticated', res.stdout)

    def test_auth_gdrive_skips_cleanly_when_credentials_unset(self):
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        rclone = bin_dir / 'rclone'
        rclone.write_text('#!/usr/bin/env bash\nexit 0\n')
        rclone.chmod(0o755)
        res = self.bash(
            'auth_gdrive',
            env={
                'PATH': f'{bin_dir}:{os.environ["PATH"]}',
                'GDRIVE_OAUTH_ENV': str(self.root / 'missing.env'),
                'GDRIVE_CLIENT_ID': '',
                'GDRIVE_CLIENT_SECRET': '',
            },
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn('not configured', res.stdout)
