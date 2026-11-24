"""Run the prune_versions role against throwaway directories (local, no become)."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest

REPO = Path(__file__).resolve().parents[1]
ANSIBLE_PLAYBOOK = shutil.which('ansible-playbook')


@unittest.skipUnless(ANSIBLE_PLAYBOOK, 'ansible-playbook is not installed')
class PruneVersionsRoleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pop-prune-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def make(self, relative, directory=True):
        path = self.root / relative
        if directory:
            (path / 'bin').mkdir(parents=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('x')
        return path

    def run_role(self, calls, *extra):
        imports = ''.join(textwrap.indent(textwrap.dedent(f'''\
            - name: Prune {index}
              ansible.builtin.import_role:
                name: prune_versions
              vars:
            ''') + ''.join(f'    {key}: "{value}"\n' for key, value in call.items()), '    ')
            for index, call in enumerate(calls))
        playbook = self.root / 'test.yml'
        playbook.write_text('- hosts: localhost\n  gather_facts: false\n  tasks:\n' + imports)
        env = dict(os.environ, ANSIBLE_ROLES_PATH=str(REPO / 'ansible/roles'),
                   ANSIBLE_LOCALHOST_WARNING='false', ANSIBLE_INVENTORY_UNPARSED_WARNING='false',
                   ANSIBLE_NOCOLOR='1')
        env.pop('ANSIBLE_CONFIG', None)
        result = subprocess.run([ANSIBLE_PLAYBOOK, '-i', 'localhost,', '-c', 'local', str(playbook), *extra],
                                cwd=self.root, env=env, capture_output=True, text=True, timeout=180)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_removes_old_versions_but_keeps_active_hidden_and_unmatched_entries(self):
        versions = self.root / 'apps/.tool.versions'
        for name in ('1.0', '1.1', 'install.abc123', '2.0', '.tmp-pop-tool-x'):
            self.make(f'apps/.tool.versions/{name}')
        for name in ('Logic-2.4.48-linux-x64.AppImage', 'Logic-2.4.50-linux-x64.AppImage', 'notes.txt'):
            self.make(f'apps/{name}', directory=False)
        self.make('apps/Logic-old-folder')  # a directory, so file mode never matches it

        self.run_role([
            {'prune_versions_dir': versions, 'prune_versions_keep': '2.0'},
            {'prune_versions_dir': self.root / 'apps', 'prune_versions_keep': 'Logic-2.4.50-linux-x64.AppImage',
             'prune_versions_type': 'file', 'prune_versions_patterns': 'Logic-*-linux-x64.AppImage'},
        ])

        self.assertEqual(sorted(p.name for p in versions.iterdir()), ['.tmp-pop-tool-x', '2.0'])
        self.assertEqual(sorted(p.name for p in (self.root / 'apps').iterdir()),
                         ['.tool.versions', 'Logic-2.4.50-linux-x64.AppImage', 'Logic-old-folder', 'notes.txt'])

    def test_nothing_is_removed_when_the_active_version_is_missing(self):
        versions = self.root / '.tool.versions'
        for name in ('1.0', '1.1'):
            self.make(f'.tool.versions/{name}')
        self.run_role([{'prune_versions_dir': versions, 'prune_versions_keep': '2.0'}])
        self.assertEqual(sorted(p.name for p in versions.iterdir()), ['1.0', '1.1'])

    def test_check_mode_reports_but_removes_nothing_and_rerun_is_unchanged(self):
        versions = self.root / '.tool.versions'
        for name in ('1.0', '2.0'):
            self.make(f'.tool.versions/{name}')
        call = [{'prune_versions_dir': versions, 'prune_versions_keep': '2.0'}]
        out = self.run_role(call, '--check')
        self.assertIn('changed=1', out)
        self.assertEqual(sorted(p.name for p in versions.iterdir()), ['1.0', '2.0'])
        self.run_role(call)
        self.assertIn('changed=0', self.run_role(call))
        self.assertEqual([p.name for p in versions.iterdir()], ['2.0'])


if __name__ == '__main__':
    unittest.main()
