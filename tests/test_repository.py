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
user_launcher = importlib.import_module('user_launcher')


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

    def test_dotfiles_replacement_restores_gh_credential_helper(self):
        dotfiles = self.root / '.dotfiles'
        dotfiles.mkdir()
        (dotfiles / 'bootstrap.sh').write_text(
            '#!/bin/bash\nprintf "[user]\\n\\tname = Dotfiles\\n" > "$HOME/.gitconfig"\n')
        result = self.bash('''
export HOME="$1" GIT_CONFIG_GLOBAL="$1/.gitconfig" GIT_CONFIG_NOSYSTEM=1
unset GIT_CONFIG_COUNT
GIT_NAME='Test User' GIT_EMAIL=test@example.com
DOTFILES_REPO=https://example.invalid/dotfiles
selected=git,dotfiles
update_repository() { :; }
gh() {
  [[ $* == 'auth setup-git --hostname github.com --force' ]]
  git config --global credential.https://github.com.helper '!gh auth git-credential'
}
setup_dotfiles
[[ $(git config --global user.name) == "$GIT_NAME" ]]
[[ $(git config --global user.email) == "$GIT_EMAIL" ]]
[[ $(git config --global credential.https://github.com.helper) == '!gh auth git-credential' ]]
''', self.root, script='pop_os_config.sh')
        self.assertEqual(result.returncode, 0, result.stderr)

    def private_dotfiles(self, repo='https://example.invalid/private.git', *, signed_in=True, restore=True):
        if restore:
            checkout = self.root / '.dotfiles-private'
            checkout.mkdir()
            (checkout / 'restore.sh').write_text('printf restored > "$HOME/restored"\n')
        return self.bash('''
export HOME="$1" PRIVATE_DOTFILES_REPO="$2" PRIVATE_DOTFILES_REF="$3"
unset GIT_CONFIG_COUNT
gh() { [[ $* == 'auth status --hostname github.com' && $GH_SIGNED_IN == 1 ]]; }
update_repository() {
  printf 'clone %s %s %s\\n' "$1" "$2" "$3" >> "$HOME/calls"
  printf 'helper %s %s=%s prompt=%s\\n' "$GIT_CONFIG_COUNT" "$GIT_CONFIG_KEY_0" "$GIT_CONFIG_VALUE_0" "$GIT_TERMINAL_PROMPT" >> "$HOME/calls"
}
optional 'Private dotfiles restore' setup_private_dotfiles
printf '%s' "${OPTIONAL_FAILURES[*]}"
[[ -z ${GIT_CONFIG_COUNT:-} ]]
''', self.root, repo, 'abc123', script='pop_os_config.sh',
            env={'GH_SIGNED_IN': '1' if signed_in else '0'})

    def google_drive(self, *, authorized, filters='', remote_client='id'):
        bin_dir = self.root / 'bin'
        bin_dir.mkdir(exist_ok=True)
        rclone = bin_dir / 'rclone'
        rclone.write_text('#!/bin/bash\nprintf "rclone %s\\n" "$*" >> "$HOME/calls"\n'
                          'if [[ $1 == listremotes && $AUTHORIZED == 1 ]]; then printf "gdrive:\\n"; fi\n'
                          'if [[ $1 == config && $2 == dump ]]; then printf \'{"gdrive":{"client_id":"%s"}}\' "$REMOTE_CLIENT"; fi\n')
        rclone.chmod(0o755)
        return self.bash('''
export HOME="$1" XDG_STATE_HOME="$1/state" GDRIVE_LOCAL_DIR="$1/Drive 100%" GDRIVE_CLIENT_ID=id GDRIVE_CLIENT_SECRET=secret GDRIVE_FILTERS_FILE="$2"
systemctl() { printf 'systemctl %s\\n' "$*" >> "$HOME/calls"; }
optional 'Google Drive sync' install_google_drive
printf '%s' "${OPTIONAL_FAILURES[*]}"
''', self.root, filters, script='pop_os_config.sh',
            env={'AUTHORIZED': '1' if authorized else '0', 'REMOTE_CLIENT': remote_client, 'PATH': f'{bin_dir}:{os.environ["PATH"]}'})

    def test_google_drive_unauthorized_writes_units_and_is_an_optional_failure(self):
        result = self.google_drive(authorized=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.endswith('Google Drive sync'))
        self.assertIn('--stages google-drive', result.stderr)
        service = (self.root / '.config/systemd/user/rclone-gdrive-bisync.service').read_text()
        self.assertIn(f'ExecStart="{self.root}/bin/rclone" "bisync" "gdrive:" "{self.root}/Drive 100%%" ', service)
        self.assertIn('"--drive-skip-gdocs"', service)
        self.assertIn('OnUnitActiveSec=15min', (self.root / '.config/systemd/user/rclone-gdrive-bisync.timer').read_text())
        self.assertNotIn('--resync', (self.root / 'calls').read_text())
        self.assertNotIn('enable', (self.root / 'calls').read_text())

    def test_google_drive_resyncs_once_then_enables_timer(self):
        for attempt in range(2):
            result = self.google_drive(authorized=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(result.stdout.endswith('Google Drive sync'), result.stderr)
        calls = (self.root / 'calls').read_text().splitlines()
        resyncs = [call for call in calls if '--resync' in call]
        self.assertEqual(len(resyncs), 1)
        self.assertIn(f'rclone bisync gdrive: {self.root}/Drive 100% ', resyncs[0])
        self.assertTrue(resyncs[0].endswith('--resync --resync-mode newer'))
        self.assertEqual(calls.count('systemctl --user enable --now rclone-gdrive-bisync.timer'), 2)

    def test_google_drive_filters_reach_the_unit_and_changes_force_a_resync(self):
        filters = self.root / 'filters.txt'
        filters.write_text('- Backups/**\n')
        for rules in ('- Backups/**\n', '- Backups/**\n', '- Backups/**\n- *.tmp\n'):
            filters.write_text(rules)
            result = self.google_drive(authorized=True, filters=filters)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(result.stdout.endswith('Google Drive sync'), result.stderr)
        service = (self.root / '.config/systemd/user/rclone-gdrive-bisync.service').read_text()
        self.assertIn(f'"--filters-file" "{filters}"', service)
        resyncs = [call for call in (self.root / 'calls').read_text().splitlines() if '--resync' in call]
        self.assertEqual(len(resyncs), 2)
        self.assertIn(f'--filters-file {filters} --resync', resyncs[1])

    def test_google_drive_missing_filters_file_is_an_optional_failure(self):
        result = self.google_drive(authorized=True, filters=self.root / 'missing.txt')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.endswith('Google Drive sync'))
        self.assertIn('GDRIVE_FILTERS_FILE does not exist', result.stderr)
        self.assertNotIn('--resync', (self.root / 'calls').read_text() if (self.root / 'calls').exists() else '')

    def test_google_drive_changed_client_id_needs_a_terminal_to_switch(self):
        result = self.google_drive(authorized=True, remote_client='')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.endswith('Google Drive sync'))
        self.assertIn('GDRIVE_CLIENT_ID differs from the gdrive remote', result.stderr)
        calls = (self.root / 'calls').read_text()
        self.assertNotIn('config update', calls)
        self.assertNotIn('--resync', calls)

    def test_systemd_quote_rejects_quotes(self):
        result = self.bash('systemd_quote \'a"b\'', script='pop_os_config.sh')
        self.assertNotEqual(result.returncode, 0)

    def test_private_dotfiles_clone_with_gh_token_and_restore(self):
        result = self.private_dotfiles()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[-1:], ['[INFO] Private dotfiles restored. Sign in to agy, claude '
                                                          'and codex separately; logins are not restored.'])
        self.assertEqual((self.root / 'calls').read_text().splitlines(), [
            f'clone https://example.invalid/private.git {self.root}/.dotfiles-private abc123',
            'helper 1 credential.https://github.com.helper=!gh auth git-credential prompt=0'])
        self.assertEqual((self.root / 'restored').read_text(), 'restored')

    def test_private_dotfiles_unconfigured_is_a_quiet_skip(self):
        result = self.private_dotfiles(repo='', restore=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('skipping private dotfiles', result.stdout)
        self.assertNotIn('Private dotfiles restore', result.stdout + result.stderr)
        self.assertFalse((self.root / 'calls').exists())

    def test_private_dotfiles_without_gh_sign_in_is_an_optional_failure(self):
        result = self.private_dotfiles(signed_in=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--stages private-dotfiles", result.stderr)
        self.assertTrue(result.stdout.endswith('Private dotfiles restore'))
        self.assertFalse((self.root / 'calls').exists())
        self.assertFalse((self.root / 'restored').exists())

    def test_private_dotfiles_without_restore_script_is_an_optional_failure(self):
        result = self.private_dotfiles(restore=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('has no restore.sh', result.stderr)
        self.assertTrue(result.stdout.endswith('Private dotfiles restore'))

    def test_private_dotfiles_stage_is_listed_and_selected_by_default(self):
        result = subprocess.run(['bash', str(REPO / 'pop_os_config.sh'), '--list-stages'], capture_output=True, text=True)
        self.assertIn('private-dotfiles', result.stdout.splitlines())
        source = (REPO / 'pop_os_config.sh').read_text()
        self.assertIn(",copilot,private-dotfiles,google-drive' stage", source)

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

    def fastfetch_install(self, candidate, *, present=False, download_status=0):
        return self.bash('''
WORK_DIR="$1"
available="$3"
command() {
    if [[ $1 == -v && $2 == fastfetch ]]; then
        [[ $available == 1 ]]
    else builtin command "$@"; fi
}
# Model policy output independently of the test host repositories.
apt-cache() { printf '  Candidate: %s\n' "$FASTFETCH_CANDIDATE"; }
sudo() { printf '%s\n' "$*"; available=1; }
fetch_locked() { printf 'fetch %s\n' "$1"; return "$FASTFETCH_DOWNLOAD_STATUS"; }
install_deb() { printf 'install %s\n' "${1##*/}"; available=1; }
install_fastfetch
''', self.root, candidate, int(present), download_status, script='pop_os_config.sh',
            env={'FASTFETCH_CANDIDATE': candidate,
                 'FASTFETCH_DOWNLOAD_STATUS': str(download_status)})

    def test_fastfetch_uses_apt_when_a_candidate_exists(self):
        result = self.fastfetch_install('2.0.0')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'apt-get install -y fastfetch')

    def test_fastfetch_uses_verified_download_without_apt_candidate(self):
        result = self.fastfetch_install('(none)')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[-2:], ['fetch fastfetch', 'install fastfetch.deb'])

    def test_fastfetch_download_failure_stops_installation(self):
        result = self.fastfetch_install('(none)', download_status=47)
        self.assertEqual(result.returncode, 47)
        self.assertEqual(result.stdout.splitlines()[-1], 'fetch fastfetch')

    def test_existing_fastfetch_skips_package_installation(self):
        result = self.fastfetch_install('(none)', present=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')

    def install_local_deb(self, apt_status=0):
        workspace = self.root / 'private'
        workspace.mkdir(mode=0o700)
        package = workspace / 'tool.deb'
        package.write_bytes(b'fixture package')
        stage = self.root / 'apt-stage'
        result = self.bash('''
dpkg-deb() { printf fixture; }
package_installed() { return 0; }
sudo() {
    case "$1" in
        mktemp) mkdir -m 0700 "$DEB_TEST_STAGE"; printf '%s' "$DEB_TEST_STAGE";;
        apt-get)
            [[ ${*: -1} == "$DEB_TEST_STAGE/package.deb" ]] || return 90
            [[ $(stat -c %a "$DEB_TEST_STAGE") == 755 ]] || return 91
            [[ $(stat -c %a "$DEB_TEST_STAGE/package.deb") == 644 ]] || return 92
            cmp "$DEB_TEST_ORIGINAL" "$DEB_TEST_STAGE/package.deb" || return 93
            printf 'APT read staged package\n'
            return "$DEB_TEST_APT_STATUS";;
        *) "$@";;
    esac
}
install_deb "$1"
''', package, env={'DEB_TEST_STAGE': str(stage), 'DEB_TEST_ORIGINAL': str(package),
                   'DEB_TEST_APT_STATUS': str(apt_status)})
        return result, workspace, package, stage

    def test_local_deb_stages_readable_copy_without_exposing_workspace(self):
        result, workspace, package, stage = self.install_local_deb()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('APT read staged package', result.stdout)
        self.assertEqual(workspace.stat().st_mode & 0o777, 0o700)
        self.assertTrue(package.exists())
        self.assertFalse(stage.exists())

    def test_failed_local_deb_install_removes_staging_and_preserves_status(self):
        result, workspace, package, stage = self.install_local_deb(48)
        self.assertEqual(result.returncode, 48, result.stderr)
        self.assertEqual(workspace.stat().st_mode & 0o777, 0o700)
        self.assertTrue(package.exists())
        self.assertFalse(stage.exists())

    def test_workstation_tmux_uses_the_dotfiles_plugin_directory(self):
        result = self.bash('''
export HOME="$1"
update_repository() { printf '%s\n' "$2"; }
optional() { printf '%s\n' "$2"; }
setup_tmux
''', self.root, script='pop_os_config.sh')
        directory = self.root / '.local/share/tmux/plugins/tpm'
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [str(directory), str(directory / 'bin/install_plugins')])

    def gcc_selection(self, candidate='16.2.0', cxx_candidate='16.2.0'):
        return self.bash('''
apt-cache() {
    case "$1" in
        pkgnames) printf '%s\\n' gcc-9 gcc-14 gcc-16 g++-9 g++-14 g++-16 gcc-99-aarch64-linux-gnu gcc-20;;
        policy)
            printf '%s:\\n' "$2"
            case "$2" in
                gcc-16) printf '  Candidate: %s\\n' "$GCC_CANDIDATE";;
                g++-16) printf '  Candidate: %s\\n' "$CXX_CANDIDATE";;
                *) printf '  Candidate: 14.2.0\\n';;
            esac;;
    esac
}
latest_native_gcc
''', script='pop_os_config.sh', env={'GCC_CANDIDATE': candidate, 'CXX_CANDIDATE': cxx_candidate})

    def test_latest_gcc_selects_highest_matching_native_pair(self):
        result = self.gcc_selection()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), '16')

    def test_latest_gcc_skips_unavailable_c_and_cxx_candidates(self):
        for gcc, cxx in [('(none)', '16.2.0'), ('16.2.0', '(none)')]:
            with self.subTest(gcc=gcc, cxx=cxx):
                result = self.gcc_selection(gcc, cxx)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), '14')

    def test_latest_gcc_rejects_empty_repositories(self):
        result = self.bash('apt-cache() { return 0; }; latest_native_gcc', script='pop_os_config.sh')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('No installable native GCC/G++ pair', result.stderr)

    def test_gcc_candidate_does_not_accept_apt_pattern_matches(self):
        result = self.bash('''
apt-cache() { printf 'unrelated-package:\\n  Candidate: 99.0\\n'; }
apt_candidate gcc-99
''', script='pop_os_config.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')

    def compiler_tools(self, major):
        directory = self.root / 'compiler-bin'
        directory.mkdir(exist_ok=True)
        for name in ('gcc', 'g++', 'gcc-ar', 'gcc-nm', 'gcc-ranlib', 'gcov', 'gcov-dump', 'gcov-tool'):
            path = directory / f'{name}-{major}'
            path.write_text('#!/bin/sh\nexit 0\n')
            path.chmod(0o755)
        return directory

    def test_native_compiler_links_upgrade_together(self):
        binaries = self.compiler_tools('14')
        self.compiler_tools('16')
        result = self.bash('''
export HOME="$1"
configure_native_gcc 14 "$2"
configure_native_gcc 16 "$2"
''', self.root, binaries, script='pop_os_config.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        for name, target in [('gcc', 'gcc'), ('g++', 'g++'), ('cc', 'gcc'), ('c++', 'g++'),
                             ('gcc-ar', 'gcc-ar'), ('gcc-nm', 'gcc-nm'), ('gcc-ranlib', 'gcc-ranlib'),
                             ('gcov', 'gcov'), ('gcov-dump', 'gcov-dump'), ('gcov-tool', 'gcov-tool')]:
            self.assertEqual((self.root / '.local/bin' / name).readlink(), binaries / f'{target}-16')

    def test_native_compiler_links_preserve_foreign_commands(self):
        binaries = self.compiler_tools('16')
        directory = self.root / '.local/bin'
        directory.mkdir(parents=True)
        foreign = directory / 'gcc'
        foreign.write_text('custom wrapper')
        result = self.bash('export HOME="$1"; configure_native_gcc 16 "$2"',
                           self.root, binaries, script='pop_os_config.sh')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(foreign.read_text(), 'custom wrapper')
        self.assertEqual(list(directory.iterdir()), [foreign])

    def test_build_tools_do_not_explicitly_request_old_compilers(self):
        result = self.bash('''
latest_native_gcc() { printf '16\\n'; }
sudo() { printf '%s\\n' "$@"; }
configure_native_gcc() { printf 'selected:%s\\n' "$1"; }
install_build_tools
''', script='pop_os_config.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        packages = result.stdout.splitlines()
        for package in ('build-essential', 'binutils', 'gdb', 'make', 'dpkg-dev', 'libc6-dev', 'gcc-16', 'g++-16'):
            self.assertIn(package, packages)
        self.assertIn('selected:16', packages)
        self.assertNotIn('gcc-14', packages)
        self.assertNotIn('g++-14', packages)

    def test_nasmfmt_install_uses_pinned_version_and_local_bin(self):
        result = self.bash(r'''
export HOME="$1"
go() {
  [[ "$GOBIN" == "$HOME/.local/bin" ]]
  [[ "$1" == install ]]
  [[ "$2" == "github.com/yamnikov-oleg/nasmfmt@$NASMFMT_VERSION" ]]
  printf '#!/bin/sh\nexit 0\n' > "$GOBIN/nasmfmt"
  chmod +x "$GOBIN/nasmfmt"
}
install_nasmfmt
[[ -x "$HOME/.local/bin/nasmfmt" ]]
''', self.root, script='pop_os_config.sh')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_nasmfmt_install_propagates_go_failure(self):
        result = self.bash(r'''
export HOME="$1"
go() { return 42; }
check_executable() { printf 'should not run'; }
if install_nasmfmt; then exit 0; else exit "$?"; fi
''', self.root, script='pop_os_config.sh')
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assertNotIn('should not run', result.stdout)

    def test_failed_build_tool_install_does_not_change_compiler_commands(self):
        result = self.bash('''
latest_native_gcc() { printf '16\\n'; }
sudo() { return 42; }
configure_native_gcc() { printf 'should not run'; }
if install_build_tools; then exit 0; else exit "$?"; fi
''', script='pop_os_config.sh')
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assertNotIn('should not run', result.stdout)

    def test_zsh_completion_install_refreshes_standard_and_xdg_caches(self):
        result = self.bash(r'''
export HOME="$1" XDG_CACHE_HOME="$1/cache"
mkdir -p "$XDG_CACHE_HOME/zsh"
touch "$HOME/.zcompdump" "$HOME/.zcompdump.zwc" \
  "$XDG_CACHE_HOME/zsh/zcompdump-5.9" "$XDG_CACHE_HOME/zsh/keep"
completion_file "$HOME/.zsh/completion/_example" printf '#compdef example\n'
[[ ! -e "$HOME/.zcompdump" && ! -e "$HOME/.zcompdump.zwc" ]]
[[ ! -e "$XDG_CACHE_HOME/zsh/zcompdump-5.9" ]]
[[ -e "$XDG_CACHE_HOME/zsh/keep" ]]
touch "$HOME/.zcompdump"
if completion_file "$HOME/.zsh/completion/_example" false; then exit 1; fi
[[ -e "$HOME/.zcompdump" ]]
''', self.root, script='pop_os_config.sh')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_ai_completions_install_and_preserve_on_generator_failure(self):
        result = self.bash(r'''
export HOME="$1"
mkdir -p "$HOME/.local/bin"
export PATH="$HOME/.local/bin:$PATH"
printf '#!/bin/sh\necho "# generated $2"\n' > "$HOME/.local/bin/codex"
chmod +x "$HOME/.local/bin/codex"
for cli in codex claude agy; do install_ai_completions "$cli"; done
printf '#!/bin/sh\necho partial\nexit 42\n' > "$HOME/.local/bin/codex"
install_ai_completions codex
[[ ${#OPTIONAL_FAILURES[@]} == 3 ]]
''', self.root, script='pop_os_config.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        for cli in ('codex', 'claude', 'agy'):
            for relative in (f'.local/share/bash-completion/completions/{cli}',
                             f'.zsh/completion/_{cli}',
                             f'.config/fish/completions/{cli}.fish'):
                content = (self.root / relative).read_text()
                self.assertTrue(content)
                self.assertNotIn('partial', content)
        self.assertFalse(list(self.root.rglob('*.tmp.*')))

    def ai_stage(self, stage, *, status=0, present=False):
        directory = self.root / '.local/bin'
        directory.mkdir(parents=True)
        work = self.root / 'work'
        work.mkdir()
        source = self.root / 'source'
        source.mkdir()
        native = source / 'antigravity'
        native.write_text('#!/bin/sh\necho fixture-version\nexit ' + str(status) + '\n')
        native.chmod(0o755)
        archive = self.root / 'antigravity.tar.gz'
        with tarfile.open(archive, 'w:gz') as stream:
            stream.add(native, arcname='antigravity')
        name = {'antigravity': 'agy', 'claudecode': 'claude', 'codex': 'codex', 'copilot': 'copilot'}[stage]
        if present:
            (directory / name).write_bytes(native.read_bytes())
            (directory / name).chmod(0o755)
        return self.bash('''
export HOME="$1" WORK_DIR="$1/work"
export PATH="$HOME/.local/bin:$PATH"
# Isolate these names from the host's installed AI clients.
command() {
    if [[ $1 == -v && $2 =~ ^(agy|claude|codex|copilot)$ ]]; then
        [[ -x "$HOME/.local/bin/$2" ]] || return 1
        printf '%s\\n' "$HOME/.local/bin/$2"
    else builtin command "$@"; fi
}
fetch_locked() { cp "$HOME/antigravity.tar.gz" "$2"; }
ensure_node_runtime() { :; }
npm() {
    [[ $1 == install && $2 == --global && $3 == --prefix && $5 == --include=optional ]] || return 90
    local binary
    case "${*: -1}" in
        "@openai/codex@$CODEX_VERSION") binary="$4/lib/node_modules/@openai/codex/bin/codex.js";;
        "@github/copilot@$COPILOT_VERSION")
            [[ $6 == --ignore-scripts=false ]] || return 92
            binary="$4/lib/node_modules/@github/copilot/npm-loader.js";;
        *) return 93;;
    esac
    mkdir -p "$(dirname "$binary")"
    printf 'console.log("fixture-version"); process.exit(%s);\\n' "$AI_TEST_STATUS" > "$binary"
}
install_repository() { printf '%s\\n' "$@" > "$HOME/repository-args"; }
sudo() {
    [[ $* == 'apt-get install -y claude-code' ]] || return 91
    cp "$HOME/source/antigravity" "$HOME/.local/bin/claude"
}
case "$2" in
    antigravity) install_antigravity_cli;;
    claudecode) install_claude_code;;
    codex) install_codex_cli;;
    copilot) install_copilot_cli;;
esac
''', self.root, stage, script='pop_os_config.sh', env={'AI_TEST_STATUS': str(status)})

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

    def test_ai_cli_stages_are_listed(self):
        result = subprocess.run(['bash', str(REPO / 'pop_os_config.sh'), '--list-stages'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        for stage in ('antigravity', 'claudecode', 'codex', 'copilot'):
            self.assertIn(stage, result.stdout.splitlines())

    def test_antigravity_stage_installs_and_verifies_native_cli(self):
        result = self.ai_stage('antigravity')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / '.local/bin/agy').is_file())
        self.assertFalse((self.root / '.local/bin/antigravity').exists())
        self.assertFalse((self.root / '.local/share/applications').exists())

    def test_antigravity_failed_native_check_does_not_publish(self):
        result = self.ai_stage('antigravity', status=42)
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assertFalse((self.root / '.local/bin/agy').exists())
        self.assertFalse((self.root / '.local/share/pop-workstation/antigravity-cli').exists())

    def test_codex_stage_installs_and_verifies_pinned_package(self):
        result = self.ai_stage('codex')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / '.local/bin/codex').is_file())
        self.assertIn('Verified codex CLI', result.stdout)

    def test_codex_failed_native_check_does_not_publish(self):
        result = self.ai_stage('codex', status=42)
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assertFalse((self.root / '.local/bin/codex').exists())
        self.assertFalse((self.root / '.local/share/pop-workstation/codex').exists())

    def test_copilot_stage_installs_and_verifies_pinned_package(self):
        result = self.ai_stage('copilot')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / '.local/bin/copilot').is_file())
        self.assertIn('Verified copilot CLI', result.stdout)

    def test_copilot_failed_native_check_does_not_publish(self):
        result = self.ai_stage('copilot', status=42)
        self.assertEqual(result.returncode, 42, result.stderr)
        self.assertFalse((self.root / '.local/bin/copilot').exists())
        self.assertFalse((self.root / '.local/share/pop-workstation/copilot').exists())

    def test_claude_stage_uses_official_latest_signed_repository(self):
        result = self.ai_stage('claudecode')
        self.assertEqual(result.returncode, 0, result.stderr)
        source = (self.root / 'repository-args').read_text()
        self.assertIn('claude-code-key', source)
        self.assertIn('signed-by=/etc/apt/keyrings/claude-code.asc', source)
        self.assertIn('https://downloads.claude.ai/claude-code/apt/latest latest main', source)
        self.assertIn('Verified claude CLI', result.stdout)

    def test_github_cli_uses_official_signed_repository(self):
        result = self.bash('''
package_installed() { return 1; }
install_repository() { printf '%s\\n' "$@" > "$HOME/repository-args"; }
sudo() { [[ $* == 'apt-get install -y gh' ]] || return 91; }
command() { [[ $1 == -v && $2 == gh ]] || builtin command "$@"; }
install_github_cli
''', script='pop_os_config.sh', env={'HOME': str(self.root)})
        self.assertEqual(result.returncode, 0, result.stderr)
        source = (self.root / 'repository-args').read_text()
        self.assertIn('githubcli-key', source)
        self.assertIn('signed-by=/etc/apt/keyrings/githubcli.gpg', source)
        self.assertIn('https://cli.github.com/packages stable main', source)

    def git_stage(self, authenticated):
        return self.bash('''
export GIT_CONFIG_GLOBAL="$1/.gitconfig" GIT_CONFIG_NOSYSTEM=1
GIT_NAME='Test User' GIT_EMAIL=test@example.com GH_LOG="$1/gh-calls" GH_STATUS=$2
install_github_cli() { :; }
gh() { printf '%s\\n' "$*" >> "$GH_LOG"; [[ $* != 'auth status --hostname github.com' ]] || return "$GH_STATUS"; }
setup_git
''', self.root, 0 if authenticated else 1, script='pop_os_config.sh')

    def test_git_stage_configures_gh_helper_before_sign_in(self):
        result = self.git_stage(authenticated=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = (self.root / 'gh-calls').read_text().splitlines()
        self.assertEqual(calls[0], 'auth setup-git --hostname github.com --force')
        self.assertIn('gh auth login --scopes workflow', result.stdout)

    def test_git_stage_skips_sign_in_when_gh_is_authenticated(self):
        result = self.git_stage(authenticated=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('already authenticated', result.stdout)
        calls = (self.root / 'gh-calls').read_text().splitlines()
        self.assertFalse(any(call.startswith('auth login') for call in calls))

    def test_ai_existing_cli_is_verified_without_installation(self):
        for stage in ('antigravity', 'claudecode', 'codex', 'copilot'):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                # Give each case its own HOME and download workspace.
                previous = self.root
                self.root = Path(directory)
                try:
                    result = self.ai_stage(stage, present=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertFalse((self.root / '.local/share/pop-workstation').exists())
                    self.assertFalse((self.root / 'repository-args').exists())
                finally:
                    self.root = previous

    def test_codex_runtime_can_be_installed_without_language_stage(self):
        runtime = self.root / 'node-runtime'
        (runtime / 'bin').mkdir(parents=True)
        for name in ('node', 'npm'):
            path = runtime / 'bin' / name
            path.write_text('#!/bin/sh\nexit 0\n')
            path.chmod(0o755)
        result = self.bash('''
export HOME="$1"
command() {
    if [[ $1 == -v && ($2 == node || $2 == npm) ]]; then
        [[ ${PATH%%:*} == "$HOME/node-runtime/bin" ]] || return 1
        printf '%s\\n' "$HOME/node-runtime/bin/$2"
    else builtin command "$@"; fi
}
mise() {
    case $1 in
        use|settings) printf '%s\\n' "$@";;
        where) printf '%s\\n' "$HOME/node-runtime";;
        *) return 99;;
    esac
}
ensure_node_runtime
printf 'node-path:%s\\n' "${PATH%%:*}"
''', self.root, script='pop_os_config.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('node@', result.stdout)
        self.assertIn('self_update.auto=true', result.stdout)
        self.assertIn('node-path:' + str(runtime / 'bin'), result.stdout)
        self.assertNotIn('go@', result.stdout)

    def test_codex_runtime_download_failure_is_preserved(self):
        result = self.bash('''
command() {
    if [[ $1 == -v && ($2 == node || $2 == npm || $2 == mise) ]]; then return 1; fi
    builtin command "$@"
}
fetch_locked() { return 43; }
export WORK_DIR="$1"
if ensure_node_runtime; then exit 0; else exit "$?"; fi
''', self.root, script='pop_os_config.sh')
        self.assertEqual(result.returncode, 43, result.stderr)

    def test_user_cli_launcher_preserves_cwd_arguments_and_runtime(self):
        binary = self.root / "runtime with spaces'quote"
        binary.write_text('#!/bin/sh\nprintf "%s\\n" "$PWD" "$@"\n')
        binary.chmod(0o755)
        wrapper = self.root / 'launcher'
        user_launcher.write(wrapper, [str(binary), 'script with spaces.js'])
        project = self.root / 'project'
        project.mkdir()
        result = subprocess.run([str(wrapper), 'file name.asm'], cwd=project, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [str(project), 'script with spaces.js', 'file name.asm'])

    def test_neovim_setup_uses_explicit_tool_verification(self):
        config = self.root / '.config/nvim'
        (config / 'scripts').mkdir(parents=True)
        (config / 'lua').mkdir()
        (config / 'scripts/setup.sh').touch()
        (config / 'lua/setup_checks.lua').touch()
        result = self.bash('''
export HOME="$1"
optional() { printf '%s\\n' "$@"; }
initialize_neovim_config
''', self.root, script='pop_os_config.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [
            'Neovim tools installation and verification', 'env',
            'NVIM_BIN=' + str(self.root / '.local/bin/nvim'), 'bash',
            str(config / 'scripts/setup.sh'), '--install'])

    def test_neovim_foreign_config_uses_bounded_startup(self):
        result = self.bash('''
export HOME="$1"
optional() { printf '%s\\n' "$@"; }
initialize_neovim_config
''', self.root, script='pop_os_config.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [
            'Neovim plugin initialization (language tools unverified)',
            'timeout', '--foreground', '5m', str(self.root / '.local/bin/nvim'),
            '--headless', '-c', 'quitall'])

    def test_rust_toolchain_explicitly_installs_and_verifies_editor_components(self):
        result = self.bash('''
rustup() { printf '%s\\n' "$*"; }
install_rust_toolchain
''', script='pop_os_config.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        version = dict(line.split('=', 1) for line in (REPO / 'resources/tool-versions.env').read_text().splitlines())['RUST_VERSION']
        lines = result.stdout.splitlines()
        self.assertIn(f'component add --toolchain {version} rust-analyzer rustfmt clippy rust-src', lines)
        for tool in ('rustc', 'cargo', 'rust-analyzer', 'rustfmt'):
            self.assertIn(f'run {version} {tool} --version', lines)
        self.assertIn(f'run {version} cargo clippy --version', lines)
        self.assertEqual(lines[-1], f'default {version}')

    def test_failed_rust_component_install_keeps_previous_default_toolchain(self):
        result = self.bash('''
rustup() {
    [[ $1 != component ]] || return 44
    printf '%s\\n' "$*"
}
if install_rust_toolchain; then exit 0; else exit "$?"; fi
''', script='pop_os_config.sh')
        self.assertEqual(result.returncode, 44, result.stderr)
        self.assertNotIn('default ', result.stdout)

    def test_arm_stage_installs_arm32_and_aarch64_toolchains(self):
        result = self.bash('sudo() { printf "%s\\n" "$@"; }; install_arm_toolchain', script='pop_os_config.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        packages = result.stdout.splitlines()
        for package in ('gcc-arm-none-eabi', 'binutils-arm-none-eabi',
                        'gcc-aarch64-linux-gnu', 'binutils-aarch64-linux-gnu', 'gdb-multiarch'):
            self.assertIn(package, packages)
        self.assertNotIn('gcc-avr', packages)
        self.assertNotIn('avrdude', packages)

    def test_avr_stage_does_not_install_arm_toolchains(self):
        result = self.bash('sudo() { printf "%s\\n" "$@"; }; install_avr_toolchain', script='pop_os_config.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        packages = result.stdout.splitlines()
        for package in ('gcc-avr', 'binutils-avr', 'avr-libc', 'avrdude', 'gdb-avr'):
            self.assertIn(package, packages)
        self.assertNotIn('gcc-arm-none-eabi', packages)
        self.assertNotIn('gcc-aarch64-linux-gnu', packages)

    def test_legacy_arm_avr_stage_runs_both_installers(self):
        result = self.bash('''
install_arm_toolchain() { printf 'arm\n'; }
install_avr_toolchain() { printf 'avr\n'; }
install_avr_and_arm_toolchain
''', script='pop_os_config.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ['arm', 'avr'])

    def test_dotfiles_delegates_plugins_only_when_tmux_stage_is_selected(self):
        checkout = self.root / '.dotfiles'
        checkout.mkdir()
        (checkout / 'bootstrap.sh').write_text('printf "%s" "$DOTFILES_SKIP_TMUX"\n')
        for stages, expected in (('dotfiles', '0'), ('dotfiles,tmux', '1')):
            with self.subTest(stages=stages):
                result = self.bash('''
export HOME="$1"
unset DOTFILES_SKIP_TMUX
DOTFILES_REPO=fixture
selected="$2"
update_repository() { :; }
setup_dotfiles
''', self.root, stages, script='pop_os_config.sh')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, expected)

    def test_identity_is_required_instead_of_using_author_defaults(self):
        result = self.bash('unset GIT_NAME GIT_EMAIL; validate_config git', script='pop_os_config.sh')
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

    def test_bash_integration_enables_apt_completion(self):
        if not Path('/usr/share/bash-completion/completions/apt').exists():
            self.skipTest('bash-completion is not installed')
        result = subprocess.run(['bash', '--noprofile', '--norc', '-ic', '''
command() {
    if [[ $1 == -v && $2 == mise ]]; then return 1; fi
    builtin command "$@"
}
source "$1"
source "$1"
_completion_loader apt
COMP_WORDS=(apt ins)
COMP_CWORD=1
COMP_LINE="apt ins"
COMP_POINT=${#COMP_LINE}
_apt
printf '%s\n' "${COMPREPLY[@]}"
''', 'test', str(REPO / 'resources/shell.bash')],
            env=dict(os.environ, HOME=str(self.root)), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('install', result.stdout.splitlines())

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

    def test_secondary_btrfs_fstab_rendering_is_idempotent(self):
        fstab = self.root / 'fstab'
        fstab.write_text(
            '# Header comment\n'
            'UUID=root-uuid\t/\tbtrfs\tdefaults,subvol=@\t0\t0\n'
            'UUID=old-uuid\t/mnt/data\tbtrfs\tdefaults,subvol=@data\t0\t0\n'
            'UUID=old-uuid\t/mnt/data/.snapshots\tbtrfs\tdefaults,subvol=@snapshots\t0\t0\n'
        )
        result = self.bash('render_secondary_fstab "$1" new-uuid-1234', fstab,
                           script='setup_secondary_btrfs_drive.sh')
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
''', script='setup_secondary_btrfs_drive.sh')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Refusing to operate on system disk', result.stderr)

    def test_secondary_btrfs_requires_format_flag_for_non_btrfs_partition(self):
        result = self.bash('''
lsblk() { printf '/dev/nvme1n1p1 part\\n'; }
blkid() { printf 'ext4\\n'; }
locate_existing_btrfs_partition /dev/nvme1n1
''', script='setup_secondary_btrfs_drive.sh')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('pass --format', result.stderr)

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
''', home, mnt_data, script='setup_secondary_btrfs_drive.sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((home / 'data').is_symlink())
        self.assertEqual((home / 'data').resolve(), mnt_data.resolve())
        self.assertTrue((home / 'Downloads').is_symlink())
        self.assertEqual((home / 'Downloads').resolve(), (mnt_data / 'Downloads').resolve())
        self.assertEqual((mnt_data / 'Downloads/file.txt').read_text(), 'saved-download')


if __name__ == '__main__':
    unittest.main()
