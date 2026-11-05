"""Antigravity guardrail merge: temporary settings files only, never the user's HOME."""
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
permissions = importlib.import_module('antigravity_permissions')
RULES = permissions.ASK_RULES


class AntigravityPermissionsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'antigravity-cli' / 'settings.json'

    def tearDown(self):
        self.temp.cleanup()

    def write(self, value, mode=0o600):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(value if isinstance(value, str) else json.dumps(value))
        os.chmod(self.path, mode)

    def read(self):
        return json.loads(self.path.read_text())

    def test_creates_missing_file_with_private_mode(self):
        self.assertEqual(permissions.apply(self.path), RULES)
        self.assertEqual(self.read(), {'permissions': {'ask': RULES}})
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_preserves_other_settings_allow_rules_and_mode(self):
        original = {'model': 'x', 'trustedWorkspaces': ['/w'],
                    'permissions': {'allow': ['command(git push origin main)', 'command(ls)'],
                                    'deny': ['command(rm -rf)'], 'ask': ['command(curl)']}}
        self.write(original, 0o640)
        permissions.apply(self.path)
        result = self.read()
        self.assertEqual(result['model'], 'x')
        self.assertEqual(result['trustedWorkspaces'], ['/w'])
        self.assertEqual(result['permissions']['allow'], original['permissions']['allow'])
        self.assertEqual(result['permissions']['deny'], ['command(rm -rf)'])
        self.assertEqual(result['permissions']['ask'], ['command(curl)'] + RULES)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o640)

    def test_second_run_changes_nothing(self):
        permissions.apply(self.path)
        before = self.path.read_bytes()
        self.assertEqual(permissions.apply(self.path), [])
        self.assertEqual(self.path.read_bytes(), before)

    def test_adds_only_missing_rules(self):
        self.write({'permissions': {'ask': ['command(git push)']}})
        self.assertEqual(permissions.apply(self.path), RULES[1:])
        self.assertEqual(self.read()['permissions']['ask'].count('command(git push)'), 1)

    def test_malformed_files_are_left_untouched(self):
        for content in ['{not json', '[]', '{"permissions": []}', '{"permissions": {"ask": "x"}}']:
            with self.subTest(content=content):
                self.write(content)
                with self.assertRaises(permissions.SettingsError):
                    permissions.apply(self.path)
                self.assertEqual(self.path.read_text(), content)

    def test_command_line_reports_failure_without_traceback(self):
        self.write('{not json')
        result = subprocess.run([sys.executable, '-B', str(SCRIPTS / 'antigravity_permissions.py'), str(self.path)],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Antigravity guardrails not applied', result.stderr)
        self.assertNotIn('Traceback', result.stderr)


if __name__ == '__main__':
    unittest.main()
