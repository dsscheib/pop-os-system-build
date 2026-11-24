"""Unit tests for scripts/auth.sh."""
import configparser
import os
from pathlib import Path
import pty
import select
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

    def test_auth_chezmoi_falls_back_to_mise_exec(self):
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        mise = bin_dir / 'mise'
        mise.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$*" > "$HOME/mise.log"\n')
        mise.chmod(0o755)
        gh = bin_dir / 'gh'
        gh.write_text('#!/usr/bin/env bash\nexit 1\n')
        gh.chmod(0o755)

        res = self.bash(
            'auth_chezmoi',
            env={
                'PATH': f'{bin_dir}:/usr/bin:/bin',
                'DOTFILES_REPO': 'https://github.com/example/dotfiles.git',
            },
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(
            (self.root / 'mise.log').read_text().strip(),
            'exec chezmoi -- chezmoi init --apply https://github.com/example/dotfiles.git',
        )

    def test_gdrive_oauth_env_parses_as_data_without_shell_expansion(self):
        oauth = self.root / 'gdrive-oauth.env'
        pwned = self.root / 'pwned'
        oauth.write_text(
            '# OAuth credentials\n'
            'export GDRIVE_CLIENT_ID="id-$LITERAL"\n'
            f"GDRIVE_CLIENT_SECRET='$(touch {pwned})'\n"
        )
        oauth.chmod(0o600)
        res = self.bash(
            'load_gdrive_oauth_env && printf "%s\\n%s\\n" "$GDRIVE_CLIENT_ID" "$GDRIVE_CLIENT_SECRET"',
            env={'GDRIVE_OAUTH_ENV': str(oauth)},
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout.splitlines(), ['id-$LITERAL', f'$(touch {pwned})'])
        self.assertFalse(pwned.exists())

        bad = self.root / 'bad-oauth.env'
        bad.write_text('UNRELATED_VAR=oops\n')
        bad.chmod(0o600)
        bad_res = self.bash('load_gdrive_oauth_env', env={'GDRIVE_OAUTH_ENV': str(bad)})
        self.assertNotEqual(bad_res.returncode, 0)

    def test_gdrive_remote_setup_keeps_client_secret_out_of_rclone_argv(self):
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        rclone_log = self.root / 'rclone.log'
        rclone = bin_dir / 'rclone'
        rclone.write_text(
            '#!/usr/bin/env bash\n'
            'printf "%s\\n" "$*" >> "$RCLONE_LOG"\n'
            'if [[ $1 == listremotes ]]; then\n'
            '  if [[ -f "$RCLONE_CONFIG" ]]; then grep -E "^\\[.+\\]$" "$RCLONE_CONFIG" | tr -d "[]" | sed "s/$/:/"; fi\n'
            'elif [[ "$1 ${2:-}" == "config file" ]]; then\n'
            '  printf "Configuration file is stored at:\\n%s\\n" "$RCLONE_CONFIG"\n'
            'fi\n'
        )
        rclone.chmod(0o755)
        rclone_conf = self.root / 'rclone.conf'

        master, slave = pty.openpty()
        proc = subprocess.Popen(
            [
                'bash', '-euo', 'pipefail', '-c',
                f'source {shlex.quote(str(AUTH_SH))}\nswitch_or_create_gdrive_remote',
            ],
            cwd=self.root,
            env=dict(
                os.environ,
                HOME=str(self.root),
                PATH=f'{bin_dir}:/usr/bin:/bin',
                RCLONE_CONFIG=str(rclone_conf),
                RCLONE_LOG=str(rclone_log),
                GDRIVE_CLIENT_ID='cid-123',
                GDRIVE_CLIENT_SECRET='super%secret$value',
            ),
            stdin=slave,
            stdout=slave,
            stderr=slave,
            close_fds=True,
        )
        os.close(slave)
        output = bytearray()
        while True:
            ready, _, _ = select.select([master], [], [], 10)
            if not ready:
                break
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            output.extend(chunk)
        os.close(master)
        rc = proc.wait(timeout=10)
        self.assertEqual(rc, 0, output.decode(errors='replace'))
        self.assertEqual(rclone_conf.stat().st_mode & 0o777, 0o600)
        cfg = configparser.RawConfigParser()
        cfg.read(rclone_conf, encoding='utf-8')
        self.assertEqual(cfg.get('gdrive', 'client_id'), 'cid-123')
        self.assertEqual(cfg.get('gdrive', 'client_secret'), 'super%secret$value')
        logged = rclone_log.read_text()
        self.assertIn('config reconnect gdrive:', logged)
        self.assertNotIn('super%secret$value', logged)

    def test_auth_gdrive_timer_check_requires_matching_unit_and_warns_on_enable_failure(self):
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        (bin_dir / 'rclone').write_text(
            '#!/usr/bin/env bash\n'
            'case $1 in\n'
            '  listremotes) printf "gdrive:\\n" ;;\n'
            '  config) printf \'{"gdrive":{"client_id":"cid"}}\' ;;\n'
            'esac\n'
        )
        (bin_dir / 'rclone').chmod(0o755)
        (bin_dir / 'jq').write_text('#!/usr/bin/env bash\ncat >/dev/null\nprintf "cid\\n"\n')
        (bin_dir / 'jq').chmod(0o755)

        sc_log = self.root / 'systemctl.log'
        (bin_dir / 'systemctl').write_text(
            '#!/usr/bin/env bash\n'
            'printf "%s\\n" "$*" >> "$SC_LOG"\n'
            'if [[ $2 == list-unit-files ]]; then\n'
            '  if [[ -n ${UNIT_LINE:-} ]]; then printf "%s\\n" "$UNIT_LINE"; fi\n'
            '  exit 0\n'
            'fi\n'
            'exit "${ENABLE_RC:-0}"\n'
        )
        (bin_dir / 'systemctl').chmod(0o755)

        state_dir = self.root / 'state'
        marker = state_dir / 'pop-workstation/gdrive-bisync-initialized'
        marker.parent.mkdir(parents=True)
        marker.write_text('gdrive: ' + str(self.root / 'GoogleDrive') + ' filters=none\n')

        base_env = {
            'PATH': f'{bin_dir}:{os.environ["PATH"]}',
            'XDG_STATE_HOME': str(state_dir),
            'GDRIVE_OAUTH_ENV': str(self.root / 'missing.env'),
            'GDRIVE_CLIENT_ID': 'cid',
            'GDRIVE_CLIENT_SECRET': 'sec',
            'SC_LOG': str(sc_log),
        }
        empty_res = self.bash('auth_gdrive', env=dict(base_env, UNIT_LINE=''))
        self.assertEqual(empty_res.returncode, 0, empty_res.stderr)
        self.assertNotIn('enable --now', sc_log.read_text())

        sc_log.unlink()
        fail_res = self.bash(
            'auth_gdrive',
            env=dict(base_env, UNIT_LINE='rclone-gdrive-bisync.timer disabled', ENABLE_RC='1'),
        )
        self.assertEqual(fail_res.returncode, 0, fail_res.stderr)
        self.assertIn('enable --now rclone-gdrive-bisync.timer', sc_log.read_text())
        self.assertIn('Could not enable rclone-gdrive-bisync.timer', fail_res.stderr)

    def test_write_gdrive_rclone_config_preserves_other_remotes(self):
        conf = self.root / 'rclone.conf'
        conf.write_text(
            '[b2]\n'
            'type = b2\n'
            'account = acct-123\n\n'
            '[gdrive]\n'
            'type = drive\n'
            'token = {"access_token":"old"}\n'
        )
        res = self.bash(
            'write_gdrive_rclone_config',
            env={
                'RCLONE_CONFIG': str(conf),
                'GDRIVE_REMOTE': 'gdrive',
                'GDRIVE_CLIENT_ID': 'new-cid',
                'GDRIVE_CLIENT_SECRET': 'new-sec',
            },
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        written = conf.read_text()
        self.assertIn('[b2]', written)
        self.assertIn('account = acct-123', written)
        self.assertIn('client_id = new-cid', written)
        self.assertNotIn('access_token', written)

    def test_write_gdrive_rclone_config_refuses_unreadable_or_unparseable_config(self):
        encrypted = self.root / 'encrypted.conf'
        encrypted_body = '# Encrypted rclone configuration File\nRCLONE_ENCRYPT_V0:opaque\n'
        encrypted.write_text(encrypted_body)
        enc_res = self.bash(
            'write_gdrive_rclone_config',
            env={
                'RCLONE_CONFIG': str(encrypted),
                'GDRIVE_REMOTE': 'gdrive',
                'GDRIVE_CLIENT_ID': 'cid',
                'GDRIVE_CLIENT_SECRET': 'sec',
            },
        )
        self.assertNotEqual(enc_res.returncode, 0)
        self.assertIn('Refusing to overwrite', enc_res.stderr)
        self.assertEqual(encrypted.read_text(), encrypted_body)

        if os.geteuid() != 0:
            locked = self.root / 'locked.conf'
            locked_body = '[other]\ntype = s3\n'
            locked.write_text(locked_body)
            locked.chmod(0o000)
            try:
                lock_res = self.bash(
                    'write_gdrive_rclone_config',
                    env={
                        'RCLONE_CONFIG': str(locked),
                        'GDRIVE_REMOTE': 'gdrive',
                        'GDRIVE_CLIENT_ID': 'cid',
                        'GDRIVE_CLIENT_SECRET': 'sec',
                    },
                )
                self.assertNotEqual(lock_res.returncode, 0)
                self.assertIn('Refusing to overwrite', lock_res.stderr)
            finally:
                locked.chmod(0o600)
            self.assertEqual(locked.read_text(), locked_body)

    def test_write_gdrive_rclone_config_uses_rclone_config_file_path(self):
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        custom_conf = self.root / 'legacy/.rclone.conf'
        custom_conf.parent.mkdir(parents=True)
        custom_conf.write_text('[other]\ntype = s3\n')
        (bin_dir / 'rclone').write_text(
            '#!/usr/bin/env bash\n'
            'if [[ "$1 ${2:-}" == "config file" ]]; then\n'
            '  if [[ -n ${FAIL_CONFIG_FILE:-} ]]; then exit 1; fi\n'
            '  printf "Configuration file is stored at:\\n%s\\n" "$CUSTOM_RCLONE_CONF"\n'
            'fi\n'
        )
        (bin_dir / 'rclone').chmod(0o755)

        res = self.bash(
            'unset RCLONE_CONFIG; write_gdrive_rclone_config',
            env={
                'PATH': f'{bin_dir}:/usr/bin:/bin',
                'CUSTOM_RCLONE_CONF': str(custom_conf),
                'GDRIVE_REMOTE': 'gdrive',
                'GDRIVE_CLIENT_ID': 'custom-cid',
                'GDRIVE_CLIENT_SECRET': 'custom-sec',
            },
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        written = custom_conf.read_text()
        self.assertIn('[other]', written)
        self.assertIn('client_id = custom-cid', written)
        self.assertFalse((self.root / '.config/rclone/rclone.conf').exists())

        fallback_conf = self.root / 'fallback.conf'
        fallback_res = self.bash(
            'rclone_config_path',
            env={
                'PATH': f'{bin_dir}:/usr/bin:/bin',
                'CUSTOM_RCLONE_CONF': str(custom_conf),
                'RCLONE_CONFIG': str(fallback_conf),
                'FAIL_CONFIG_FILE': '1',
            },
        )
        self.assertEqual(fallback_res.returncode, 0, fallback_res.stderr)
        self.assertEqual(fallback_res.stdout.strip(), str(fallback_conf))
