"""Render group_vars defaults with Ansible for a given Ubuntu release (no become, no host changes)."""
import json
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
DEFAULTS = REPO / 'ansible/group_vars/all/00-defaults.yml'
ANSIBLE_PLAYBOOK = shutil.which('ansible-playbook')


@unittest.skipUnless(ANSIBLE_PLAYBOOK, 'ansible-playbook is not installed')
class GroupVarsTests(unittest.TestCase):
    def render(self, variable, **extra_vars):
        with tempfile.TemporaryDirectory(prefix='pop-group-vars-test-') as temp:
            playbook, output = Path(temp) / 'render.yml', Path(temp) / 'rendered.json'
            playbook.write_text(
                '- hosts: localhost\n'
                '  gather_facts: false\n'
                f'  vars_files: [{json.dumps(str(DEFAULTS))}]\n'
                '  tasks:\n'
                '    - ansible.builtin.copy:\n'
                f'        content: "{{{{ {variable} | to_json }}}}"\n'
                f'        dest: {json.dumps(str(output))}\n'
                '        mode: "0600"\n')
            env = dict(os.environ, ANSIBLE_LOCALHOST_WARNING='false',
                       ANSIBLE_INVENTORY_UNPARSED_WARNING='false', ANSIBLE_NOCOLOR='1')
            env.pop('ANSIBLE_CONFIG', None)
            result = subprocess.run(
                [ANSIBLE_PLAYBOOK, '-i', 'localhost,', '-c', 'local', str(playbook),
                 '-e', json.dumps(extra_vars)],
                cwd=temp, env=env, capture_output=True, text=True, timeout=180)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return json.loads(output.read_text())

    def test_hyper_backup_deps_use_the_libxml2_name_of_each_release(self):
        for version, expected, absent in (('24.04', 'libxml2', 'libxml2-16'),
                                          ('26.04', 'libxml2-16', 'libxml2'),
                                          ('26.10', 'libxml2-16', 'libxml2')):
            with self.subTest(version=version):
                packages = self.render('apt_hyper_backup_deps', ubuntu_version=version)
                self.assertIn(expected, packages)
                self.assertNotIn(absent, packages)
                self.assertIn('libxslt1.1', packages)


if __name__ == '__main__':
    unittest.main()
