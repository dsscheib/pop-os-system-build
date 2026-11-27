"""Repository regressions: filesystem fixtures and stubs, never real installers."""
import hashlib
import importlib
import gzip
import io
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
import warnings
import xml.etree.ElementTree as ET
import zipfile
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
assets = importlib.import_module('st_assets')
snapper_config = importlib.import_module('configure_snapper')


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

    def test_st_preflight_skips_sudo_check_when_st_skip_udev_is_set(self):
        path = self.root / 'bin'
        path.mkdir()
        for cmd in ('python3', 'sha256sum', 'realpath', 'find', 'mktemp', 'ldd', 'desktop-file-validate'):
            tool = path / cmd
            tool.write_text('#!/bin/sh\nexit 0\n')
            tool.chmod(0o755)
        skipped = self.bash(
            'require_supported_system() { return 0; }\nPATH="$1"\nhash -r\nst_preflight',
            path,
            env={'ST_SKIP_UDEV': '1'},
        )
        self.assertEqual(skipped.returncode, 0, skipped.stderr)

        required = self.bash(
            'require_supported_system() { return 0; }\nPATH="$1"\nhash -r\nst_preflight',
            path,
        )
        self.assertNotEqual(required.returncode, 0)
        self.assertIn('sudo', required.stderr)

    def test_st_finish_skips_stlink_udev_when_st_skip_udev_is_set(self):
        exe = self.root / 'app'
        exe.write_text('#!/bin/sh\nexit 0\n')
        exe.chmod(0o755)
        result = self.bash('''
SCRIPT_DIR="$3"
install_stlink_rules() { return 99; }
WORK_DIR="$1/work"
INSTALL_DIR="$1/inst"
INSTALL_TARGET="$1/target"
mkdir -p "$WORK_DIR" "$INSTALL_DIR"
HOME="$1/home"
mkdir -p "$HOME"
st_finish TestApp "$HOME/.local/bin/testapp" "$2" "$INSTALL_DIR" icon wmclass "$HOME/.local/share/applications/testapp.desktop"
''', self.root, exe, REPO, env={'ST_SKIP_UDEV': '1'})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / 'home/.local/bin/testapp').is_file())
        self.assertTrue((self.root / 'home/.local/share/applications/testapp.desktop').is_file())

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

    def test_zip_rejects_chained_symlink_escape_and_absolute_symlink(self):
        chained = self.root / 'chained.zip'
        with zipfile.ZipFile(chained, 'w') as archive:
            m1 = zipfile.ZipInfo('sub/dir')
            m1.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(m1, '..')
            m2 = zipfile.ZipInfo('sub/dir/escape')
            m2.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(m2, '..')
        with self.assertRaises(ValueError):
            assets.extract_zip(chained, self.root / 'extracted-chained')
        self.assertFalse((self.root / 'extracted-chained/escape').exists())

        abs_link = self.root / 'abs.zip'
        with zipfile.ZipFile(abs_link, 'w') as archive:
            m = zipfile.ZipInfo('abs')
            m.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(m, '/etc/passwd')
        with self.assertRaises(ValueError):
            assets.extract_zip(abs_link, self.root / 'extracted-abs')

    def test_zip_duplicate_symlink_member_does_not_write_outside_root(self):
        dup = self.root / 'dup.zip'
        outside = self.root / 'outside.txt'
        outside.write_text('untouched')
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            with zipfile.ZipFile(dup, 'w') as archive:
                m1 = zipfile.ZipInfo('item')
                m1.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(m1, 'safe-target')
                m2 = zipfile.ZipInfo('item')
                m2.external_attr = (stat.S_IFREG | 0o644) << 16
                archive.writestr(m2, 'replacement')
        extracted = self.root / 'extracted-dup'
        assets.extract_zip(dup, extracted)
        self.assertFalse((extracted / 'item').is_symlink())
        self.assertEqual((extracted / 'item').read_text(), 'replacement')
        self.assertEqual(outside.read_text(), 'untouched')

    def test_zip_final_pass_removes_deferred_escaping_symlink(self):
        crafted = self.root / 'deferred-escape.zip'
        with zipfile.ZipFile(crafted, 'w') as archive:
            # First link looks inside root because `a/b/d` does not exist yet;
            # the second symlink makes `d -> .`, turning `d/../../..` into parent of root.
            escape_link = zipfile.ZipInfo('a/b/link')
            escape_link.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(escape_link, 'd/../../..')
            pivot_link = zipfile.ZipInfo('a/b/d')
            pivot_link.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(pivot_link, '.')
        extracted = self.root / 'extracted-deferred'
        with self.assertRaisesRegex(ValueError, 'Unsafe archive symlink'):
            assets.extract_zip(crafted, extracted)
        self.assertFalse((extracted / 'a/b/link').is_symlink())
        self.assertFalse((extracted / 'a/b/link').exists())

    def test_tar_extracts_valid_internal_relative_symlink(self):
        tar_path = self.root / 'valid.tar'
        with tarfile.open(tar_path, 'w') as archive:
            file_info = tarfile.TarInfo('lib/libfoo.so.1')
            payload = b'elf'
            file_info.size = len(payload)
            file_info.mode = 0o755
            archive.addfile(file_info, io.BytesIO(payload))
            link_info = tarfile.TarInfo('lib/libfoo.so')
            link_info.type = tarfile.SYMTYPE
            link_info.linkname = 'libfoo.so.1'
            archive.addfile(link_info)
        extracted = self.root / 'extracted-tar'
        with tarfile.open(tar_path) as archive:
            assets.extract_tar(archive, extracted)
        self.assertTrue((extracted / 'lib/libfoo.so').is_symlink())
        self.assertEqual((extracted / 'lib/libfoo.so').read_bytes(), b'elf')

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

    def test_cube_install_copies_adjacent_jre_and_rejects_missing_runtime(self):
        for script, template, payload, runtime in (
            ('install_stm32_cube_mx.sh', 'cubemx', 'SetupSTM32CubeMX-test', 'jre'),
            ('install_stm32_cube_prog.sh', 'cubeprogrammer',
             'SetupSTM32CubeProgrammer-test.exe', 'bin/jre'),
        ):
            for copy_runtime in ('yes', 'no'):
                with self.subTest(script=script, copy_runtime=copy_runtime):
                    fixture = self.root / f'{template}-{copy_runtime}'
                    fixture.mkdir()
                    jar = fixture / payload
                    self.jar_fixture(jar, REPO / f'resources/izpack/{template}.xml')
                    archive = fixture / 'archive.zip'
                    java = '''#!/usr/bin/env bash
set -euo pipefail
[[ $1 == -Djava.awt.headless=true && $2 == -jar ]] || exit 41
[[ $PWD == "${3%/*}" && -x jre/bin/java ]] || exit 43
python3 - "$4" <<'PY'
import os
from pathlib import Path
import shutil
import sys
import xml.etree.ElementTree as ET
target = Path(ET.parse(sys.argv[1]).find('.//installpath').text)
for name in ('STM32CubeMX', 'bin/STM32_Programmer_CLI', 'bin/STM32CubeProgrammerLauncher'):
    executable = target / name
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_text('#!/bin/sh\\nexit 0\\n')
    executable.chmod(0o755)
if os.environ['TEST_COPY_RUNTIME'] == 'yes':
    shutil.copytree('jre', target / os.environ['TEST_RUNTIME'])
PY
'''
                    with zipfile.ZipFile(archive, 'w') as container:
                        container.write(jar, payload)
                        info = zipfile.ZipInfo('jre/bin/java')
                        info.external_attr = (stat.S_IFREG | 0o755) << 16
                        container.writestr(info, java)
                    target = fixture / 'installed'
                    target.mkdir()
                    (target / 'keep').write_text('working')
                    result = self.bash('''
st_preflight() { require_commands python3 sha256sum realpath find mktemp; }
eval "$(declare -f begin_install | sed '1s/begin_install/original_begin_install/')"
begin_install() { original_begin_install "$TEST_TARGET"; }
check_elf_dependencies() { return 0; }
st_finish() { check_executable "$3"; publish_install; }
main "$1"
''', archive, script=script, env={
                        'HOME': str(fixture / 'home'),
                        'TEST_TARGET': str(target), 'TEST_RUNTIME': runtime,
                        'TEST_COPY_RUNTIME': copy_runtime,
                        'ST_INSTALLER_SHA256': hashlib.sha256(archive.read_bytes()).hexdigest(),
                    })
                    if copy_runtime == 'yes':
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                        self.assertTrue(target.is_symlink())
                        self.assertTrue(os.access(target / runtime / 'bin/java', os.X_OK))
                    else:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn('Required executable missing', result.stderr)
                        self.assertEqual((target / 'keep').read_text(), 'working')
                        self.assertFalse(target.is_symlink())
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
python3 - "$4" <<'PY'
from pathlib import Path
import shutil
import sys
import xml.etree.ElementTree as ET
directory = Path(ET.parse(sys.argv[1]).find('.//installpath').text)
binary = directory / 'STM32CubeMX'
binary.write_text('#!/bin/sh\\nexit 0\\n')
binary.chmod(0o755)
shutil.copytree('jre', directory / 'jre')
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
        self.assertIn('Native CubeMX application icon is unavailable', result.stderr)
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
                           script='preboot/setup_btrfs_snapper.sh')
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
        result = self.bash(mock, script='preboot/setup_btrfs_snapper.sh', env={'SNAPSHOT_UUID': 'root-uuid'})
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.bash(mock, script='preboot/setup_btrfs_snapper.sh', env={'SNAPSHOT_UUID': 'wrong-uuid'})
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

    def test_failed_install_after_published_stage_cleans_only_new_staging(self):
        result = self.bash('''
start_workspace install-test
begin_install "$1/first"
printf preserved > "$INSTALL_DIR/content"
publish_install
begin_install "$1/second"
printf partial > "$INSTALL_DIR/content"
exit 42
''', self.root)
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assertEqual((self.root / 'first/content').read_text(), 'preserved')
        self.assertFalse((self.root / 'second').exists())
        self.assertEqual(list((self.root / '.second.versions').iterdir()), [])

    def test_staging_directory_failure_preserves_previous_published_install(self):
        result = self.bash('''
start_workspace install-test
begin_install "$1/first"
printf preserved > "$INSTALL_DIR/content"
publish_install
mkdir() { return 43; }
begin_install "$1/second"
''', self.root)
        self.assertEqual(result.returncode, 43, result.stderr)
        self.assertEqual((self.root / 'first/content').read_text(), 'preserved')

    def test_secondary_btrfs_fstab_rendering_is_idempotent(self):
        fstab = self.root / 'fstab'
        fstab.write_text(
            '# Header comment\n'
            'UUID=root-uuid\t/\tbtrfs\tdefaults,subvol=@\t0\t0\n'
            'UUID=old-uuid\t/mnt/data\tbtrfs\tdefaults,subvol=@data\t0\t0\n'
            'UUID=old-uuid\t/mnt/data/.snapshots\tbtrfs\tdefaults,subvol=@snapshots\t0\t0\n'
        )
        result = self.bash('render_secondary_fstab "$1" new-uuid-1234', fstab,
                           script='preboot/setup_secondary_btrfs_drive.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(sum('/mnt/data\t' in line for line in lines), 1)
        self.assertEqual(sum('/mnt/data/.snapshots\t' in line for line in lines), 1)
        self.assertIn(
            'UUID=new-uuid-1234\t/mnt/data\tbtrfs\t'
            'defaults,noatime,compress=zstd:1,ssd,discard=async,space_cache=v2,nofail,subvol=@data\t0\t0',
            lines,
        )
        self.assertIn(
            'UUID=new-uuid-1234\t/mnt/data/.snapshots\tbtrfs\t'
            'defaults,noatime,compress=zstd:1,ssd,discard=async,space_cache=v2,nofail,subvol=@snapshots\t0\t0',
            lines,
        )

    def test_secondary_btrfs_rejects_system_disk(self):
        result = self.bash('''
lsblk() {
  if [[ "$*" == *"-dnro TYPE"* ]]; then printf 'disk\\n';
  elif [[ "$*" == *"-dnro PKNAME"* ]]; then printf 'nvme0n1\\n'; fi
}
findmnt() { printf '/dev/nvme0n1p3[/@]\\n'; }
verify_disk_is_secondary /dev/nvme0n1
''', script='preboot/setup_secondary_btrfs_drive.sh')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Refusing to operate on system disk', result.stderr)

    def test_secondary_btrfs_requires_format_flag_for_non_btrfs_partition(self):
        result = self.bash('''
lsblk() { printf '/dev/nvme1n1p1 part\\n'; }
blkid() { printf 'ext4\\n'; }
locate_existing_btrfs_partition /dev/nvme1n1
''', script='preboot/setup_secondary_btrfs_drive.sh')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('pass --format', result.stderr)

    def test_samsung_nvme_rule_has_no_fixed_pci_address(self):
        # A fixed address such as 0000:00:1d.0 is a different device on other machines (the
        # Wi-Fi card's root port on one laptop); the port rule must follow the drive instead.
        text = (REPO / 'resources/udev/99-samsung-970-evo-nvme.rules').read_text(encoding='utf-8')
        rules = [line for line in text.splitlines() if line.strip() and not line.startswith('#')]
        self.assertFalse([line for line in rules if 'KERNEL==' in line])
        for line in rules:
            with self.subTest(rule=line):
                self.assertIn('{vendor}=="0x144d"', line)
                self.assertIn('{device}=="0xa808"', line)
        self.assertIn('RUN+="/bin/sh -c \'echo on > /sys%p/../power/control\'"', text)
        if shutil.which('udevadm'):
            result = subprocess.run(['udevadm', 'verify', str(REPO / 'resources/udev/99-samsung-970-evo-nvme.rules')],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def _fake_samsung_sysfs(self, port_control):
        port = self.root / 'devices/pci0000:00/0000:00:06.0'
        drive = port / '0000:01:00.0'
        (drive / 'power').mkdir(parents=True)
        (drive / 'link').mkdir()
        (port / 'power').mkdir()
        (port / 'vendor').write_text('0x8086\n')
        (port / 'power/control').write_text(f'{port_control}\n')
        for name, value in (('vendor', '0x144d'), ('device', '0xa808'), ('d3cold_allowed', '0'),
                            ('power/control', 'on'), ('link/l1_2_aspm', '0'), ('link/l1_2_pcipm', '0')):
            (drive / name).write_text(f'{value}\n')
        bus = self.root / 'bus'
        bus.mkdir()
        (bus / '0000:01:00.0').symlink_to(drive)
        (bus / '0000:00:06.0').symlink_to(port)
        return bus

    def test_secondary_btrfs_checks_the_port_above_the_samsung_drive(self):
        bus = self._fake_samsung_sysfs('auto')
        result = self.bash('PCI_DEVICES_DIR="$1"; verify_nvme_power_settings', bus,
                           script='preboot/setup_secondary_btrfs_drive.sh')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('0000:00:06.0/power/control=on, got auto', result.stderr)

    def test_secondary_btrfs_accepts_a_powered_port_above_the_samsung_drive(self):
        bus = self._fake_samsung_sysfs('on')
        result = self.bash('PCI_DEVICES_DIR="$1"; verify_nvme_power_settings', bus,
                           script='preboot/setup_secondary_btrfs_drive.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('not currently detected', result.stderr)

    def test_secondary_btrfs_migrates_and_symlinks_downloads_idempotently(self):
        home = self.root / 'home'
        mnt_data = self.root / 'mnt_data'
        (home / 'Downloads').mkdir(parents=True)
        (home / 'Downloads/file.txt').write_text('saved-download')
        mnt_data.mkdir(parents=True)
        result = self.bash('''
MOUNT_DATA="$2"
configure_user_links "$1" "$(id -u)" "$(id -g)"
configure_user_links "$1" "$(id -u)" "$(id -g)"
''', home, mnt_data, script='preboot/setup_secondary_btrfs_drive.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((home / 'data').is_symlink())
        self.assertEqual((home / 'data').resolve(), mnt_data.resolve())
        self.assertTrue((home / 'Downloads').is_symlink())
        self.assertEqual((home / 'Downloads').resolve(), (mnt_data / 'Downloads').resolve())
        self.assertEqual((mnt_data / 'Downloads/file.txt').read_text(), 'saved-download')

    def test_secondary_btrfs_failed_subvolume_or_umount_preserves_mounted_data(self):
        mount_root = self.root / 'mounts'
        mount_root.mkdir()
        for scenario in ('btrfs-create-fail', 'busy-umount-fail'):
            with self.subTest(scenario=scenario):
                case_root = mount_root / scenario
                case_root.mkdir()
                umount_log = self.root / f'{scenario}-umount.log'
                res = self.bash('''
export SECONDARY_MOUNT_ROOT="$1"
export SCENARIO="$2"
export UMOUNT_LOG="$3"
start_workspace secondary-btrfs
trap cleanup_secondary_workspace EXIT
mount() {
  local mnt=${@: -1}
  [[ $mnt != "$WORK_DIR"* ]] || { printf 'mountpoint inside WORK_DIR: %s\n' "$mnt" >&2; return 99; }
  printf 'live-pool-data' > "$mnt/sentinel.txt"
}
btrfs() {
  if [[ $2 == show ]]; then return 1; fi
  if [[ $2 == create && $SCENARIO == btrfs-create-fail ]]; then return 42; fi
  return 0
}
findmnt() {
  local mnt=${@: -1}
  [[ -e "$mnt/sentinel.txt" ]]
}
umount() {
  local mnt=${@: -1}
  printf '%s\n' "$mnt" >> "$UMOUNT_LOG"
  if [[ $SCENARIO == busy-umount-fail ]]; then return 16; fi
  rm -f "$mnt/sentinel.txt"
}
ensure_subvolumes /dev/nvme9n1p1
''', case_root, scenario, umount_log, script='preboot/setup_secondary_btrfs_drive.sh')
                self.assertNotEqual(res.returncode, 0)
                self.assertTrue(umount_log.is_file())
                if scenario == 'btrfs-create-fail':
                    self.assertEqual(res.returncode, 42, res.stderr)
                    self.assertEqual(list(case_root.iterdir()), [])
                else:
                    self.assertEqual(res.returncode, 1, res.stderr)
                    self.assertIn('Unmount failed', res.stderr)
                    dirs = list(case_root.iterdir())
                    self.assertEqual(len(dirs), 1)
                    self.assertEqual((dirs[0] / 'sentinel.txt').read_text(), 'live-pool-data')

    def test_secondary_btrfs_downloads_collision_preserves_existing_file(self):
        home = self.root / 'home'
        mnt_data = self.root / 'mnt_data'
        (home / 'Downloads').mkdir(parents=True)
        (mnt_data / 'Downloads').mkdir(parents=True)
        (home / 'Downloads/file.txt').write_text('new-local-data')
        (mnt_data / 'Downloads/file.txt').write_text('existing-pool-data')
        result = self.bash('''
MOUNT_DATA="$2"
configure_user_links "$1" "$(id -u)" "$(id -g)"
''', home, mnt_data, script='preboot/setup_secondary_btrfs_drive.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((home / 'Downloads').is_symlink())
        self.assertEqual((mnt_data / 'Downloads/file.txt').read_text(), 'new-local-data')
        backups = list((mnt_data / 'Downloads').glob('file.txt.~*~'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), 'existing-pool-data')

    def test_secondary_btrfs_refuses_mount_root_inside_work_dir_or_symlink(self):
        real_dir = self.root / 'real-dir'
        real_dir.mkdir()
        symlink_root = self.root / 'symlink-mount-root'
        symlink_root.symlink_to(real_dir)

        symlink_res = self.bash('''
export SECONDARY_MOUNT_ROOT="$1"
start_workspace secondary-btrfs
trap cleanup_secondary_workspace EXIT
ensure_subvolumes /dev/nvme9n1p1
''', symlink_root, script='preboot/setup_secondary_btrfs_drive.sh')
        self.assertNotEqual(symlink_res.returncode, 0)
        self.assertIn('Invalid secondary mount root', symlink_res.stderr)

        inside_res = self.bash('''
start_workspace secondary-btrfs
trap cleanup_secondary_workspace EXIT
mkdir -p "$WORK_DIR/sub"
export SECONDARY_MOUNT_ROOT="$WORK_DIR/sub"
ensure_subvolumes /dev/nvme9n1p1
''', script='preboot/setup_secondary_btrfs_drive.sh')
        self.assertNotEqual(inside_res.returncode, 0)
        self.assertIn('must stay outside WORK_DIR', inside_res.stderr)

    def test_secondary_btrfs_downloads_migration_refuses_mounted_disk(self):
        home = self.root / 'home'
        mnt_data = self.root / 'mnt_data'
        usb_mount = home / 'Downloads/usb'
        usb_mount.mkdir(parents=True)
        mnt_data.mkdir(parents=True)
        (home / 'Downloads/local.txt').write_text('local-file')
        (usb_mount / 'keep.txt').write_text('external-disk-file')

        for mount_point in (usb_mount.resolve(), (home / 'Downloads').resolve()):
            with self.subTest(mount_point=mount_point):
                res = self.bash('''
MOUNT_DATA="$2"
FAKE_MOUNT="$3"
findmnt() {
  if [[ "$*" == *"-rn -o TARGET"* ]]; then
    printf '/\\n%s\\n' "$FAKE_MOUNT"
    return 0
  fi
  command findmnt "$@"
}
configure_user_links "$1" "$(id -u)" "$(id -g)"
''', home, mnt_data, mount_point, script='preboot/setup_secondary_btrfs_drive.sh')
                self.assertNotEqual(res.returncode, 0)
                self.assertIn('Refusing to migrate', res.stderr)
                self.assertTrue((home / 'Downloads').is_dir())
                self.assertFalse((home / 'Downloads').is_symlink())
                self.assertEqual((usb_mount / 'keep.txt').read_text(), 'external-disk-file')
                self.assertEqual((home / 'Downloads/local.txt').read_text(), 'local-file')

    def test_preboot_non_root_sudo_reexec_points_to_existing_script(self):
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        sudo_log = self.root / 'sudo.log'
        sudo_stub = bin_dir / 'sudo'
        sudo_stub.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "$SUDO_LOG"\n')
        sudo_stub.chmod(0o755)
        env = dict(os.environ, PATH=f'{bin_dir}:{os.environ["PATH"]}', SUDO_LOG=str(sudo_log))
        for rel_path, args, expected_flag in (
            ('preboot/setup_secondary_btrfs_drive.sh', ['--format', '/dev/nvme9n1'], '--format'),
            ('preboot/setup_btrfs_snapper.sh', ['--skip-hibernation'], '--skip-hibernation'),
        ):
            with self.subTest(script=rel_path):
                sudo_log.unlink(missing_ok=True)
                res = subprocess.run(
                    ['bash', str(REPO / rel_path), *args],
                    env=env,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(res.returncode, 0, res.stderr)
                recorded = sudo_log.read_text().splitlines()
                self.assertEqual(recorded[0], '--')
                self.assertEqual(recorded[1], str(REPO / rel_path))
                self.assertTrue(Path(recorded[1]).is_file())
                self.assertIn(expected_flag, recorded[2:])

    def test_snapper_config_skips_rewrite_when_unchanged(self):
        root = self.root / 'root-config'
        root.write_text('SUBVOLUME="/"\nALLOW_GROUPS=""\nNUMBER_LIMIT="10"\n')
        defaults = self.root / 'snapper-defaults'
        defaults.write_text('SNAPPER_CONFIGS=""\n')
        self.assertTrue(snapper_config.configure(root, defaults))
        root_stat_1 = root.stat()
        defaults_stat_1 = defaults.stat()
        self.assertFalse(snapper_config.configure(root, defaults))
        root_stat_2 = root.stat()
        defaults_stat_2 = defaults.stat()
        self.assertEqual((root_stat_1.st_ino, root_stat_1.st_mtime_ns),
                         (root_stat_2.st_ino, root_stat_2.st_mtime_ns))
        self.assertEqual((defaults_stat_1.st_ino, defaults_stat_1.st_mtime_ns),
                         (defaults_stat_2.st_ino, defaults_stat_2.st_mtime_ns))

    def test_verify_pgp_accepts_exact_single_primary_fingerprint(self):
        key = self.root / 'kicad.asc'
        key.write_text('dummy key')
        fpr = 'FDA854F61C4D0D9572BB95E5245D5502FAD7A805'
        colons = f'pub:-:4096:1:245D5502FAD7A805:1594116023:::-:::scSC::::::23::0:\nfpr:::::::::{fpr}:\n'
        observed_mode = None

        def fake_run(cmd, env=None, **_kwargs):
            nonlocal observed_mode
            self.assertIn('--with-colons', cmd)
            observed_mode = os.stat(env['GNUPGHOME']).st_mode & 0o777
            return subprocess.CompletedProcess(cmd, 0, stdout=colons, stderr='')

        with mock.patch.object(assets.subprocess, 'run', side_effect=fake_run):
            assets.verify_pgp(key, fpr.lower())
        self.assertEqual(observed_mode, 0o700)

    def test_verify_pgp_rejects_mismatch_extra_keys_or_bad_inputs(self):
        key = self.root / 'kicad.asc'
        key.write_text('dummy key')
        fpr = 'FDA854F61C4D0D9572BB95E5245D5502FAD7A805'
        other = '0' * 40

        with self.assertRaises(ValueError):
            assets.verify_pgp(key, 'not-a-fingerprint')
        with self.assertRaises(ValueError):
            assets.verify_pgp(self.root / 'missing.asc', fpr)

        bad_outputs = (
            (1, '', 'gpg error'),
            (0, f'pub:-:4096:\nfpr:::::::::{other}:\n', ''),
            (0, f'pub:-:4096:\nfpr:::::::::{fpr}:\nsub:-:4096:\n', ''),
            (0, f'pub:-:4096:\nfpr:::::::::{fpr}:\npub:-:4096:\nfpr:::::::::{fpr}:\n', ''),
        )
        for rc, stdout, stderr in bad_outputs:
            with self.subTest(rc=rc, stdout=stdout):
                proc = subprocess.CompletedProcess(['gpg'], rc, stdout=stdout, stderr=stderr)
                with mock.patch.object(assets.subprocess, 'run', return_value=proc):
                    with self.assertRaises(ValueError):
                        assets.verify_pgp(key, fpr)

    def test_stm32_install_is_current_and_publish_records_sha256(self):
        target = self.root / 'app'
        wrapper = self.root / 'bin/app'
        desktop = self.root / 'apps/app.desktop'
        wrapper.parent.mkdir(parents=True)
        desktop.parent.mkdir(parents=True)
        wrapper.write_text('wrapper')
        desktop.write_text('desktop')
        digest = 'a' * 64
        result = self.bash('''
VERIFIED_ARCHIVE_SHA256="$1"
begin_install "$2"
printf installed > "$INSTALL_DIR/marker"
publish_install
install_is_current "$2" "$3" "$4"
''', digest, target, wrapper, desktop)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((target / '.source.sha256').read_text().strip(), digest)

        mismatch = self.bash(
            'VERIFIED_ARCHIVE_SHA256="$1"; install_is_current "$2" "$3" "$4"',
            'b' * 64, target, wrapper, desktop,
        )
        self.assertNotEqual(mismatch.returncode, 0)

        forced = self.bash(
            'VERIFIED_ARCHIVE_SHA256="$1"; install_is_current "$2" "$3" "$4"',
            digest, target, wrapper, desktop,
            env={'ST_FORCE_REINSTALL': '1'},
        )
        self.assertNotEqual(forced.returncode, 0)

        wrapper.unlink()
        missing_artifact = self.bash(
            'VERIFIED_ARCHIVE_SHA256="$1"; install_is_current "$2" "$3" "$4"',
            digest, target, wrapper, desktop,
        )
        self.assertNotEqual(missing_artifact.returncode, 0)

    def test_stm32_cubemx_rerun_reports_unchanged_without_extra_version_dir(self):
        jar = self.root / 'SetupSTM32CubeMX-test'
        self.jar_fixture(jar, REPO / 'resources/izpack/cubemx.xml')
        archive = self.root / 'mx.zip'
        fake_java = '''#!/usr/bin/env bash
python3 - "$4" <<'PY'
from pathlib import Path
import shutil, sys, xml.etree.ElementTree as ET
directory = Path(ET.parse(sys.argv[1]).find('.//installpath').text)
binary = directory / 'STM32CubeMX'
binary.write_text('#!/bin/sh\\nexit 0\\n')
binary.chmod(0o755)
shutil.copytree('jre', directory / 'jre')
PY
'''
        with zipfile.ZipFile(archive, 'w') as zip_file:
            zip_file.write(jar, jar.name)
            info = zipfile.ZipInfo('jre/bin/java')
            info.external_attr = (stat.S_IFREG | 0o755) << 16
            zip_file.writestr(info, fake_java)

        home = self.root / 'home'
        home.mkdir()
        env = {
            'HOME': str(home),
            'ST_SKIP_UDEV': '1',
            'ST_INSTALLER_SHA256': hashlib.sha256(archive.read_bytes()).hexdigest(),
        }
        code = '''
st_preflight() { require_commands python3 sha256sum realpath find mktemp desktop-file-validate; }
check_elf_dependencies() { return 0; }
refresh_desktop_environment() { return 0; }
main "$1"
'''
        first = self.bash(code, archive, script='install_stm32_cube_mx.sh', env=env)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        versions_dir = home / 'STMicroelectronics/STM32Cube/.STM32CubeMX.versions'
        self.assertEqual(len(list(versions_dir.iterdir())), 1)

        second = self.bash(code, archive, script='install_stm32_cube_mx.sh', env=env)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertIn('(unchanged)', second.stdout)
        self.assertEqual(len(list(versions_dir.iterdir())), 1)

    def test_stm32_role_uses_repo_root_and_idempotent_changed_when(self):
        tasks = yaml.safe_load((REPO / 'ansible/roles/stm32/tasks/main.yml').read_text(encoding='utf-8'))
        probe = next(t for t in tasks if t['name'] == 'Check local availability of requested STMicroelectronics archives')
        self.assertEqual(probe['ansible.builtin.command']['chdir'], '{{ repo_root }}')
        run = next(t for t in tasks if t['name'] == 'Run requested STMicroelectronics user-space installers')
        self.assertIn('(unchanged)', run['changed_when'])

    def test_desktop_udev_handler_reloads_and_triggers_usb_rules(self):
        handlers = yaml.safe_load((REPO / 'ansible/roles/desktop_apps/handlers/main.yml').read_text(encoding='utf-8'))
        reload_handler = next(h for h in handlers if h['name'] == 'Reload desktop udev rules')
        self.assertEqual(
            reload_handler['ansible.builtin.command']['argv'],
            ['udevadm', 'control', '--reload-rules'],
        )
        trigger_handler = next(h for h in handlers if h.get('listen') == 'Reload desktop udev rules')
        self.assertEqual(
            trigger_handler['ansible.builtin.command']['argv'],
            ['udevadm', 'trigger', '--subsystem-match=usb'],
        )
        self.assertTrue(trigger_handler['become'])
        self.assertEqual(trigger_handler['when'], "ansible_facts['service_mgr'] == 'systemd'")

    def test_touchpad_udev_handler_reapplies_rules_to_input_devices(self):
        # The cosmic-swipe uaccess ACL only reaches an existing touchpad on a change event for
        # the input subsystem; the desktop handler's USB trigger would never apply it.
        handlers = yaml.safe_load((REPO / 'ansible/roles/desktop_apps/handlers/main.yml').read_text(encoding='utf-8'))
        trigger = next(h for h in handlers if h.get('listen') == 'Reload touchpad udev rules')
        self.assertEqual(
            trigger['ansible.builtin.command']['argv'],
            ['udevadm', 'trigger', '--subsystem-match=input', '--action=change'],
        )
        self.assertTrue(trigger['become'])

    def test_brave_default_policies_each_turn_a_feature_off(self):
        # Brave names some policies ...Disabled (true turns off) and others ...Enabled
        # (false turns off); a flipped value would switch a feature on instead.
        defaults = yaml.safe_load((REPO / 'ansible/group_vars/all/00-defaults.yml').read_text(encoding='utf-8'))
        policies = defaults['brave_policies']
        self.assertTrue(policies)
        for name, value in policies.items():
            with self.subTest(policy=name):
                self.assertIsInstance(value, bool)
                if name.endswith('Disabled'):
                    self.assertIs(value, True)
                else:
                    self.assertTrue(name.endswith('Enabled'))
                    self.assertIs(value, False)

    def test_brave_policies_are_written_where_brave_reads_managed_policies(self):
        # brave-core app/brave_main_delegate.cc sets the Linux policy dir to /etc/brave/policies.
        tasks = yaml.safe_load((REPO / 'ansible/roles/browsers/tasks/brave.yml').read_text(encoding='utf-8'))
        task = next(t for t in tasks if t['name'] == 'Write Brave managed policies')
        self.assertEqual(task['ansible.builtin.copy']['dest'], '/etc/brave/policies/managed/pop-os-system-build.json')
        self.assertIn('brave_policies | to_nice_json', task['ansible.builtin.copy']['content'])

    def test_firefox_policies_keep_app_updates_off_and_enforce_privacy(self):
        # While /etc/firefox/policies/policies.json exists, Firefox ignores the policies.json in
        # its install folder, which is where Pop's package turns self-updates off.
        defaults = yaml.safe_load((REPO / 'ansible/group_vars/all/00-defaults.yml').read_text(encoding='utf-8'))
        policies = defaults['firefox_policies']
        self.assertIs(policies['DisableAppUpdate'], True)
        self.assertIs(policies['DisableTelemetry'], True)
        self.assertIs(policies['DisableFirefoxStudies'], True)
        self.assertEqual(policies['EnableTrackingProtection'], {'Value': True, 'Category': 'strict'})
        for addon_id, settings in policies['ExtensionSettings'].items():
            with self.subTest(addon=addon_id):
                self.assertEqual(
                    settings['install_url'],
                    f'https://addons.mozilla.org/firefox/downloads/latest/{addon_id}/latest.xpi',
                )

    def test_firefox_policies_are_written_where_every_firefox_reads_them(self):
        # Firefox reads /etc/<lowercased application.ini Name>/policies/policies.json, and both
        # Pop's Firefox and Firefox Developer Edition are named "Firefox".
        tasks = yaml.safe_load((REPO / 'ansible/roles/browsers/tasks/firefox.yml').read_text(encoding='utf-8'))
        task = next(t for t in tasks if t['name'] == 'Write Firefox system policies')
        self.assertEqual(task['ansible.builtin.copy']['dest'], '/etc/firefox/policies/policies.json')
        self.assertIn("{'policies': firefox_policies}", task['ansible.builtin.copy']['content'])

    def _cosmic_swipe_tasks(self):
        tasks = yaml.safe_load((REPO / 'ansible/roles/desktop_apps/tasks/cosmic_swipe.yml').read_text(encoding='utf-8'))
        install = next(t for t in tasks if t['name'] == 'Install cosmic-swipe touchpad gestures')
        configure = next(t for t in install['block'] if t['name'] == 'Configure installed cosmic-swipe release')
        return install, configure['block']

    def test_cosmic_swipe_installs_only_on_cosmic(self):
        install, _ = self._cosmic_swipe_tasks()
        self.assertEqual(install['when'], 'desktop_apps_cosmic_comp_stat.stat.exists')

    def test_cosmic_swipe_udev_rule_sorts_before_logind_acl_rules(self):
        # uaccess tags only take effect if set before systemd's 73-seat-late.rules runs.
        _, configure = self._cosmic_swipe_tasks()
        rule = next(t for t in configure if t['name'] == 'Install touchpad-only udev rule for cosmic-swipe')
        name = Path(rule['ansible.builtin.copy']['dest']).name
        self.assertLess(name, '73-seat-late.rules')

    def test_cosmic_swipe_udev_rule_is_installed_from_the_repo(self):
        # Root copies this rule; a copy in the user-owned release folder could be edited by
        # anything running as the user and would run as root on the next apply.
        _, configure = self._cosmic_swipe_tasks()
        rule = next(t for t in configure if t['name'] == 'Install touchpad-only udev rule for cosmic-swipe')
        src = rule['ansible.builtin.copy']['src']
        self.assertEqual(src, '{{ repo_root }}/resources/udev/70-cosmic-swipe-touchpad.rules')
        text = (REPO / 'resources/udev/70-cosmic-swipe-touchpad.rules').read_text(encoding='utf-8')
        rules = [line for line in text.splitlines() if line.strip() and not line.startswith('#')]
        self.assertEqual(len(rules), 1)
        self.assertIn('ENV{ID_INPUT_TOUCHPAD}=="1"', rules[0])
        self.assertIn('TAG+="uaccess"', rules[0])
        self.assertNotIn('RUN', rules[0])

    def test_cosmic_swipe_leaves_systemd_user_dir_group_bits_to_chezmoi(self):
        # Chezmoi manages ~/.config/systemd/user with the user's umask; a fixed numeric
        # mode here made every apply stop on Chezmoi drift.
        _, configure = self._cosmic_swipe_tasks()
        task = next(t for t in configure if t['name'] == 'Ensure systemd user unit directory exists')
        self.assertEqual(task['ansible.builtin.file']['mode'], 'u=rwx')

    def test_cosmic_swipe_user_service_is_skipped_in_containers(self):
        _, configure = self._cosmic_swipe_tasks()
        service = next(t for t in configure if t['name'] == 'Enable and start cosmic-swipe user service')
        self.assertIn('systemd_live', service['tags'])

    def test_cosmic_swipe_pin_matches_release_archive_layout(self):
        # The tasks find files under cosmic-swipe-<version>-x86_64-linux/; a bump to a release
        # with a different archive name must fail here rather than on a workstation.
        entry = yaml.safe_load((REPO / 'ansible/group_vars/all/downloads.yml').read_text(encoding='utf-8'))[
            'pinned_downloads']['cosmic_swipe']
        self.assertEqual(
            Path(entry['url']).name,
            f"cosmic-swipe-{entry['version']}-x86_64-linux.tar.gz",
        )


if __name__ == '__main__':
    unittest.main()
