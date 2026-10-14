"""Repository regressions: filesystem fixtures and stubs, never real installers."""
import hashlib
import importlib
import gzip
import io
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from contextlib import redirect_stderr
import xml.etree.ElementTree as ET
import zipfile

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
assets = importlib.import_module('st_assets')
snapper_config = importlib.import_module('configure_snapper')
shell_integration = importlib.import_module('shell_integration')
neovim_wrapper = importlib.import_module('neovim_wrapper')


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pop-repo-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def bash(self, code, *arguments, script='common.sh', env=None):
        environment = dict(os.environ, TMPDIR=str(self.root))
        if env:
            environment.update(env)
        return subprocess.run(['bash', '-euo', 'pipefail', '-c',
                               f'source {shlex.quote(str(REPO / script))}\n{code}',
                               'test', *map(str, arguments)], cwd=self.root,
                              env=environment, capture_output=True, text=True)

    def test_mktemp_failure_is_not_success(self):
        result = self.bash('mktemp() { return 42; }; create_secure_tmpdir test')
        self.assertEqual(result.returncode, 42)
        self.assertEqual(result.stdout, '')

    def test_chmod_failure_removes_empty_temporary_directory(self):
        result = self.bash('chmod() { return 42; }; create_secure_tmpdir test')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_logs_do_not_interpret_backslash_sequences(self):
        result = self.bash("log_error 'path\\nname'")
        self.assertEqual(result.stderr, '[ERROR] path\\nname\n')
        self.assertEqual(result.stdout, '')

    def test_missing_explicit_installer_never_falls_back(self):
        (self.root / 'good.zip').write_text('archive')
        result = self.bash('find_archive missing.zip "*.zip"')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')

    def test_archive_search_rejects_ambiguity(self):
        for name in ('one.zip', 'two.zip'):
            (self.root / name).write_text('archive')
        result = self.bash('find_archive "" "*.zip"', env={'ST_DOWNLOAD_DIR': str(self.root)})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Multiple matching', result.stderr)

    def test_archive_patterns_overlap_without_false_ambiguity(self):
        path = self.root / 'one.sh.zip'
        path.write_text('archive')
        result = self.bash('find_archive "" "*.sh.zip" "*.zip"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(path))

    def test_explicit_bare_filename_is_canonicalized(self):
        path = self.root / 'installer.run'
        path.write_text('archive')
        result = self.bash('find_archive installer.run "*.run"')
        self.assertEqual(result.stdout.strip(), str(path))

    def test_checksum_mismatch_keeps_existing_download(self):
        destination = self.root / 'application'
        destination.write_text('working')
        result = self.bash('''
curl() { printf partial > "${@: -1}"; }
download_verified https://example.invalid/file "$1" "$(printf '%064d' 0)"
''', destination)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(destination.read_text(), 'working')
        self.assertFalse(list(self.root.glob('*.download.*')))

    def test_network_failure_keeps_existing_download(self):
        destination = self.root / 'application'
        destination.write_text('working')
        result = self.bash('curl() { return 42; }; download_verified https://example.invalid/file "$1" ignored', destination)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(destination.read_text(), 'working')

    def test_publish_preserves_old_installation_and_hidden_files(self):
        target = self.root / 'app'
        target.mkdir()
        (target / 'keep').write_text('old')
        result = self.bash('begin_install "$1"; printf new > "$INSTALL_DIR/.hidden"; publish_install', target)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(target.is_symlink())
        self.assertEqual((target / '.hidden').read_text(), 'new')
        backups = list(self.root.glob('app.backup.*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / 'keep').read_text(), 'old')

    def test_publish_failure_restores_old_installation(self):
        target = self.root / 'app'
        target.mkdir()
        (target / 'keep').write_text('old')
        result = self.bash('''
begin_install "$1"
mv() { if [[ $1 == -Tf ]]; then return 42; fi; command mv "$@"; }
publish_install
''', target)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(target.is_symlink())
        self.assertEqual((target / 'keep').read_text(), 'old')

    def test_zip_rejects_traversal_and_external_symlinks(self):
        for name, symlink in (('../escape', False), ('link', True)):
            path = self.root / 'unsafe.zip'
            with zipfile.ZipFile(path, 'w') as archive:
                info = zipfile.ZipInfo(name)
                if symlink:
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(info, '../escape' if symlink else 'bad')
            with self.subTest(name=name), self.assertRaises(ValueError):
                assets.extract_zip(path, self.root / 'extracted')
            self.assertFalse((self.root.parent / 'escape').exists())

    def test_tar_rejects_external_links(self):
        path = self.root / 'unsafe.tar'
        with tarfile.open(path, 'w') as archive:
            info = tarfile.TarInfo('link')
            info.type = tarfile.SYMTYPE
            info.linkname = '/etc'
            archive.addfile(info)
        with tarfile.open(path) as archive, self.assertRaises(ValueError):
            assets.extract_tar(archive, self.root / 'extracted')

    def test_zip_restores_executable_permissions(self):
        path = self.root / 'installer.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            info = zipfile.ZipInfo('jre/bin/java')
            info.external_attr = (stat.S_IFREG | 0o755) << 16
            archive.writestr(info, 'stub')
        assets.extract_zip(path, self.root / 'extracted')
        self.assertTrue(os.access(self.root / 'extracted/jre/bin/java', os.X_OK))

    def jar_fixture(self, path, template):
        tree = ET.parse(template)
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('resources/panelsOrder', ' '.join(x.attrib['id'] for x in tree.getroot()))
            for tag in dict.fromkeys(element.tag for element in tree.getroot()):
                archive.writestr(tag.replace('.', '/') + '.class', '')

    def test_response_xml_escapes_installation_path_and_checks_panels(self):
        template = REPO / 'resources/izpack/cubemx.xml'
        jar = self.root / 'installer.jar'
        self.jar_fixture(jar, template)
        response = self.root / 'response.xml'
        target = str(self.root / 'quotes & spaces <target>')
        assets.response(template, target, response, jar)
        self.assertEqual(ET.parse(response).find('.//installpath').text, target)
        with zipfile.ZipFile(jar, 'w') as archive:
            archive.writestr('resources/panelsOrder', '')
        with self.assertRaises(ValueError):
            assets.response(template, target, response, jar)

    def test_wrapper_preserves_arguments_with_shell_metacharacters(self):
        directory = self.root / 'space & quotes " $'
        directory.mkdir()
        executable = directory / 'application'
        executable.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@"\n')
        executable.chmod(0o755)
        wrapper = self.root / 'wrapper'
        assets.wrapper(str(wrapper), str(directory), str(executable), 'GDK_BACKEND=x11')
        result = subprocess.run([wrapper, 'two words', '$(should-not-run)', ';'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ['two words', '$(should-not-run)', ';'])
        desktop = self.root / 'application.desktop'
        assets.desktop(str(desktop), 'Application', str(executable), str(directory), 'applications-development', 'application')
        result = subprocess.run(['desktop-file-validate', desktop], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Exec="', desktop.read_text())

    def test_cube_install_failure_preserves_existing_installation(self):
        for script, template_name, payload_name in (
            ('install_stm32_cube_mx.sh', 'cubemx', 'SetupSTM32CubeMX-6.18_1'),
            ('install_stm32_cube_prog.sh', 'cubeprogrammer', 'SetupSTM32CubeProgrammer-2.23.0.exe'),
        ):
            with self.subTest(script=script):
                fixture = self.root / template_name
                fixture.mkdir()
                jar = fixture / payload_name
                self.jar_fixture(jar, REPO / f'resources/izpack/{template_name}.xml')
                archive = fixture / 'archive.zip'
                with zipfile.ZipFile(archive, 'w') as zip_file:
                    zip_file.write(jar, payload_name)
                    java = zipfile.ZipInfo('jre/bin/java')
                    java.external_attr = (stat.S_IFREG | 0o755) << 16
                    zip_file.writestr(java, '#!/usr/bin/env bash\nexit 42\n')
                target = fixture / 'installed'
                target.mkdir()
                (target / 'keep').write_text('working')
                digest = hashlib.sha256(archive.read_bytes()).hexdigest()
                code = '''
st_preflight() { require_commands python3 sha256sum realpath find mktemp; }
eval "$(declare -f begin_install | sed '1s/begin_install/original_begin_install/')"
begin_install() { original_begin_install "$TEST_TARGET"; }
main "$1"
'''
                result = self.bash(code, archive, script=script,
                                   env={'TEST_TARGET': str(target), 'ST_INSTALLER_SHA256': digest})
                self.assertEqual(result.returncode, 42, result.stderr)
                self.assertEqual((target / 'keep').read_text(), 'working')
                self.assertEqual(list((fixture / '.installed.versions').iterdir()), [])

    def test_cubeide_selects_main_package_before_failed_extraction(self):
        # A companion package appears first in the payload. Metadata, not order,
        # must choose the IDE package; extraction failure must preserve old data.
        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode='w') as tar:
            for name in ('driver.deb', 'main.deb', 'server.deb'):
                info = tarfile.TarInfo(name)
                info.size = 3
                tar.addfile(info, io.BytesIO(b'deb'))
        installer = b'#!/bin/sh\n# fixture\noffset=`head -n 4 "$0"`\n# end header\n' + payload.getvalue()
        archive = self.root / 'ide.zip'
        with zipfile.ZipFile(archive, 'w') as zip_file:
            zip_file.writestr('stm32cubeide_test.sh', installer)
        target = self.root / 'installed'
        target.mkdir()
        (target / 'keep').write_text('working')
        log = self.root / 'deb.log'
        result = self.bash('''
st_preflight() { require_commands python3 sha256sum realpath find mktemp; }
dpkg-deb() {
  if [[ $1 == -f ]]; then
    case ${2##*/} in
      driver.deb) printf st-stlink-udev-rules ;;
      main.deb) printf stm32cubeide-test ;;
      server.deb) printf st-stlink-server ;;
    esac
  else printf '%s' "$2" > "$DEB_LOG"; return 44; fi
}
main "$1"
''', archive, script='install_stm32_cube_ide.sh',
                           env={'DEB_LOG': str(log), 'ST_INSTALLER_SHA256': hashlib.sha256(archive.read_bytes()).hexdigest()})
        self.assertEqual(result.returncode, 44, result.stderr)
        self.assertTrue(log.read_text().endswith('/main.deb'))
        self.assertEqual((target / 'keep').read_text(), 'working')

    def test_sw_installer_failure_preserves_existing_installation(self):
        jar = self.root / 'installer.jar'
        self.jar_fixture(jar, REPO / 'resources/izpack/sw4stm32.xml')
        jre = io.BytesIO()
        java = b'#!/usr/bin/env bash\nexit 42\n'
        with tarfile.open(fileobj=jre, mode='w') as tar:
            info = tarfile.TarInfo('jre/bin/java')
            info.size = len(java)
            info.mode = 0o755
            tar.addfile(info, io.BytesIO(java))
        installer = self.root / 'sw.run'
        installer.write_bytes(b'#!/bin/sh\nJRE_MARKER:\n' + gzip.compress(jre.getvalue()) +
                              b'\nJAR_MARKER:\n' + jar.read_bytes())
        target = self.root / 'installed'
        target.mkdir()
        (target / 'keep').write_text('working')
        result = self.bash('''
st_preflight() { require_commands python3 sha256sum realpath find mktemp; }
eval "$(declare -f begin_install | sed '1s/begin_install/original_begin_install/')"
begin_install() { original_begin_install "$TEST_TARGET"; }
main "$1"
''', installer, script='install_sw4stm32.sh',
                           env={'TEST_TARGET': str(target), 'ST_INSTALLER_SHA256': hashlib.sha256(installer.read_bytes()).hexdigest()})
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assertEqual((target / 'keep').read_text(), 'working')
        self.assertEqual(list((self.root / '.installed.versions').iterdir()), [])

    def test_missing_cube_icon_does_not_fail_successful_install(self):
        jar = self.root / 'SetupSTM32CubeMX-test'
        self.jar_fixture(jar, REPO / 'resources/izpack/cubemx.xml')
        archive = self.root / 'mx.zip'
        fake_java = '''#!/usr/bin/env bash
python3 - "$3" <<'PY'
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
directory = Path(ET.parse(sys.argv[1]).find('.//installpath').text)
binary = directory / 'STM32CubeMX'
binary.write_text('#!/bin/sh\\nexit 0\\n')
binary.chmod(0o755)
PY
'''
        with zipfile.ZipFile(archive, 'w') as zip_file:
            zip_file.write(jar, jar.name)
            info = zipfile.ZipInfo('jre/bin/java')
            info.external_attr = (stat.S_IFREG | 0o755) << 16
            zip_file.writestr(info, fake_java)
        target = self.root / 'installed'
        result = self.bash('''
st_preflight() { require_commands python3 sha256sum realpath find mktemp; }
eval "$(declare -f begin_install | sed '1s/begin_install/original_begin_install/')"
begin_install() { original_begin_install "$TEST_TARGET"; }
st_finish() { check_executable "$3"; publish_install; }
main "$1"
''', archive, script='install_stm32_cube_mx.sh',
                           env={'TEST_TARGET': str(target), 'ST_INSTALLER_SHA256': hashlib.sha256(archive.read_bytes()).hexdigest()})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Native CubeMX icon is unavailable', result.stderr)
        self.assertTrue(target.is_symlink())
        self.assertTrue((target / 'STM32CubeMX').exists())

    def test_snapper_config_preserves_other_configs_and_retention(self):
        root = self.root / 'root-config'
        root.write_text('SUBVOLUME="/"\nALLOW_GROUPS="wheel"\nNUMBER_LIMIT="42"\n')
        defaults = self.root / 'snapper-defaults'
        defaults.write_text('# comment\nSNAPPER_CONFIGS="home database"\n')
        snapper_config.configure(root, defaults)
        values = snapper_config.read_assignments(root.read_text())
        self.assertEqual(values['NUMBER_LIMIT'], '42')
        self.assertIn('wheel', values['ALLOW_GROUPS'].split())
        self.assertEqual(snapper_config.read_assignments(defaults.read_text())['SNAPPER_CONFIGS'], 'home database root')
        self.assertEqual(snapper_config.read_assignments(defaults.read_text())['DISABLE_APT_SNAPSHOT'], 'yes')
        self.assertTrue(root.with_name('root-config.pre-pop-setup').exists())
        self.assertIn('# comment', defaults.read_text())

    def test_snapper_rejects_foreign_root_config(self):
        root = self.root / 'root-config'
        root.write_text('SUBVOLUME="/home"\n')
        with self.assertRaises(ValueError):
            snapper_config.configure(root, self.root / 'defaults')
        self.assertEqual(root.read_text(), 'SUBVOLUME="/home"\n')

    def test_snapper_initializes_empty_template_fields(self):
        root = self.root / 'root-config'
        root.write_text('SUBVOLUME=""\nFSTYPE=""\nNUMBER_LIMIT="50"\n')
        snapper_config.configure(root, self.root / 'defaults', initialize=True)
        values = snapper_config.read_assignments(root.read_text())
        self.assertEqual(values['SUBVOLUME'], '/')
        self.assertEqual(values['FSTYPE'], 'btrfs')
        self.assertEqual(values['NUMBER_LIMIT'], '10')
        self.assertEqual(values['TIMELINE_CREATE'], 'no')

    def test_snapper_rejects_incorrect_mounted_root(self):
        result = self.bash('findmnt() { if [[ $3 == FSTYPE ]]; then printf btrfs; else printf /@/nested; fi; }; verify_subvolumes',
                           script='setup_btrfs_snapper.sh')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Boot with rootflags', result.stderr)

    def test_snapper_accepts_only_matching_root_and_snapshot_mounts(self):
        mock = '''
findmnt() {
  case "$3:${@: -1}" in
    FSTYPE:*) printf btrfs ;;
    FSROOT:/) printf /@ ;;
    FSROOT:/.snapshots) printf /@snapshots ;;
    UUID:/) printf root-uuid ;;
    UUID:/.snapshots) printf '%s' "$SNAPSHOT_UUID" ;;
  esac
}
verify_subvolumes
'''
        result = self.bash(mock, script='setup_btrfs_snapper.sh', env={'SNAPSHOT_UUID': 'root-uuid'})
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.bash(mock, script='setup_btrfs_snapper.sh', env={'SNAPSHOT_UUID': 'wrong-uuid'})
        self.assertNotEqual(result.returncode, 0)

    def test_apt_configuration_parses_in_isolation(self):
        parts = self.root / 'apt-parts'
        parts.mkdir()
        (parts / '80pop-snapper').write_text((REPO / 'resources/80pop-snapper').read_text())
        config = self.root / 'bootstrap'
        config.write_text(f'Dir::Etc::parts "{parts}";\nDir::Etc::main "/dev/null";\n')
        result = subprocess.run(['apt-config', 'dump'], env=dict(os.environ, APT_CONFIG=str(config)), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count('/usr/local/libexec/pop-snapper-apt-hook pre'), 1)
        self.assertEqual(result.stdout.count('/usr/local/libexec/pop-snapper-apt-hook post'), 1)
        self.assertNotIn('/var/tmp/snapper-apt', result.stdout)

    def test_snapshot_pair_uses_the_pre_number_and_removes_state(self):
        state = self.root / 'state'
        state.mkdir()
        result = self.bash('''
snapper() {
  if [[ " $* " == *" -t pre "* ]]; then printf '123\n'; else printf '%s\n' "$*" >> "$LOG"; fi
}
LOG=$2
snapshot_pair pre "$1"
snapshot_pair post "$1"
''', state, self.root / 'snapper.log', script='scripts/snapper-apt-hook.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--pre-number=123', (self.root / 'snapper.log').read_text())
        self.assertFalse((state / 'apt-pre.num').exists())

    def test_snapshot_failure_is_reported_without_blocking_apt(self):
        state = self.root / 'state'
        state.mkdir()
        (state / 'apt-pre.num').write_text('old')
        result = self.bash('snapper() { return 42; }; snapshot_pair pre "$1"', state,
                           script='scripts/snapper-apt-hook.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Pre-snapshot failed', result.stderr)
        self.assertFalse((state / 'apt-pre.num').exists())

    def test_workstation_stage_listing_does_not_run_setup(self):
        result = subprocess.run(['bash', REPO / 'pop_os_config.sh', '--list-stages'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('languages', result.stdout.splitlines())

    def test_optional_failure_is_reported(self):
        result = self.bash('optional "test component" false; printf "%s" "${OPTIONAL_FAILURES[*]}"', script='pop_os_config.sh')
        self.assertEqual(result.returncode, 0)
        self.assertIn('test component', result.stderr)
        self.assertEqual(result.stdout, 'test component')

    def test_identity_is_required_instead_of_using_author_defaults(self):
        result = self.bash('unset GIT_NAME GIT_EMAIL GITHUB_USER; validate_config git', script='pop_os_config.sh')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Set GIT_NAME', result.stderr)

    def test_shell_integration_preserves_linked_dotfiles(self):
        checkout = self.root / 'dotfiles'
        checkout.mkdir()
        original = checkout / 'bashrc'
        original.write_text('keep my config\n')
        (self.root / '.bashrc').symlink_to(original)
        with redirect_stderr(io.StringIO()):
            shell_integration.integrate(self.root, REPO / 'resources')
        self.assertTrue((self.root / '.bashrc').is_symlink())
        self.assertEqual(original.read_text(), 'keep my config\n')
        self.assertTrue((self.root / '.config/pop-workstation/bash').exists())
        with redirect_stderr(io.StringIO()):
            shell_integration.integrate(self.root, REPO / 'resources')
        self.assertEqual((self.root / '.zshenv').read_text().count('# Pop workstation tools'), 1)

    def test_pinned_download_metadata_is_complete(self):
        values = json.loads((REPO / 'resources/downloads.json').read_text())
        for name, data in values.items():
            with self.subTest(name=name):
                self.assertTrue(data['url'].startswith('https://'))
                self.assertNotIn('/latest/', data['url'])
                self.assertRegex(data['sha256'], '^[a-f0-9]{64}$')

    def test_neovim_wrapper_preserves_project_cwd_and_arguments(self):
        binary = self.root / 'nvim'
        binary.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$PWD" "$@"\n')
        binary.chmod(0o755)
        wrapper = self.root / 'wrapper'
        provider = str(self.root / "python's directory/bin/python")
        neovim_wrapper.write(str(wrapper), str(binary), provider)
        project = self.root / 'project'
        project.mkdir()
        result = subprocess.run([wrapper, 'file with spaces.py'], cwd=project, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], str(project))
        self.assertEqual(lines[1], '--cmd')
        self.assertEqual(lines[2], "let g:python3_host_prog='" + provider.replace("'", "''") + "'")
        self.assertEqual(lines[3], 'file with spaces.py')


if __name__ == '__main__':
    unittest.main()
