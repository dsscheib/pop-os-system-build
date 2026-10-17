"""Behavior checks against real shell completion engines in an isolated HOME."""
import fcntl
import os
import pathlib
import pty
import select
import struct
import subprocess
import tempfile
import termios
import time
import shutil
import unittest


REPO = pathlib.Path(__file__).resolve().parents[1]


def run(args, env):
    result = subprocess.run(args, env=env, text=True, capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError(result.stderr)
    return result.stdout


class CompletionTests(unittest.TestCase):
    @unittest.skipUnless(all(shutil.which(tool) for tool in ('bash', 'zsh', 'fish', 'codex'))
                        and pathlib.Path('/usr/share/bash-completion/bash_completion').exists(),
                        'Requires Bash completion, Zsh, Fish, and a real Codex generator')
    def test_generated_and_bundled_completions(self):
        with tempfile.TemporaryDirectory() as directory:
            home = pathlib.Path(directory)
            env = dict(os.environ, HOME=directory,
                       XDG_CONFIG_HOME=directory + '/.config',
                       XDG_DATA_HOME=directory + '/.local/share',
                       ZDOTDIR=directory, TERM='xterm')
            # Seed the cached registry before the new completion files exist.
            run(['zsh', '-fc', 'autoload -Uz compinit; compinit -d "$HOME/.zcompdump"'], env)
            run(['bash','-c',f'source "{REPO}/pop_os_config.sh"; for cli in codex claude agy; do install_ai_completions "$cli"; done'],env)
            for cli,prefix,want in [('codex','logi','login'),('claude','doc','doctor'),('agy','mod','models')]:
                for token,expected in [(prefix,want),('--vers','--version')]:
                    function = '_codex' if cli == 'codex' else f'_pop_{cli}_complete'
                    code = f'''
source /usr/share/bash-completion/bash_completion
_completion_loader {cli}
COMP_WORDS=({cli} {token})
COMP_CWORD=1
COMP_LINE="{cli} {token}"
COMP_POINT=${{#COMP_LINE}}
{function} {cli} {token} {cli}
printf '%s\\n' "${{COMPREPLY[@]}}"
'''
                    out = run(['bash', '-c', code], env)
                    assert expected in out.split(),(cli,'bash',out)
                    out=run(['fish','-c',f'complete -C "{cli} {token}"'],env)
                    assert expected in [line.split('\t')[0] for line in out.splitlines()],(cli,'fish',out)
            # Use a real ZLE widget and Tab, then print the resulting editing buffer.
            (home/'.zshrc').write_text('fpath=("$HOME/.zsh/completion" $fpath)\nautoload -Uz compinit\ncompinit -C -d "$HOME/.zcompdump"\nbindkey "^I" complete-word\nPROMPT="READY> "\n_report() { print -r -- "RESULT:$BUFFER"; zle send-break; }\nzle -N _report\nbindkey "^X" _report\n')
            pid, fd = pty.fork()
            if pid == 0:
                os.execvpe('zsh', ['zsh', '-i'], env)
            fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 160, 0, 0))
            def read_until(marker):
                data = b''
                deadline = time.monotonic() + 8
                while marker not in data and time.monotonic()<deadline:
                    if select.select([fd], [], [], 0.1)[0]:
                        data += os.read(fd, 65536)
                assert marker in data,data
                return data
            try:
                read_until(b'READY>')
                for cli,prefix,want in [('codex','logi','login'),('claude','doc','doctor'),('agy','mod','models')]:
                    for token,expected in [(prefix,want),('--vers','--version')]:
                        os.write(fd, f'{cli} {token}\t\t'.encode())
                        time.sleep(0.15)
                        os.write(fd, b'\x18')
                        out = read_until(b'RESULT:')
                        if b'READY>' not in out:
                            out += read_until(b'READY>')
                        assert f'RESULT:{cli} {expected}'.encode() in out,out
            finally:
                os.write(fd, b'exit\n')
                os.close(fd)
                os.waitpid(pid, 0)
