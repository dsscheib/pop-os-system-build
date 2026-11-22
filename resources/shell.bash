export PATH="$HOME/.local/bin:${GOBIN:-${GOPATH:-$HOME/go}/bin}:${CARGO_HOME:-$HOME/.cargo}/bin:$PATH"
# Load programmable completion (including APT) before other shell integrations.
if [[ $- == *i* && -z ${BASH_COMPLETION_VERSINFO:-} && -r /usr/share/bash-completion/bash_completion ]]; then
  # shellcheck source=/dev/null
  source /usr/share/bash-completion/bash_completion
fi
if command -v mise >/dev/null; then eval "$(mise activate bash)"; fi
# Interactive agent CLIs run in a task- and memory-limited scope; see agent-scope.
if [[ $- == *i* ]]; then
  agy() { "$HOME/.config/pop-workstation/agent-scope" agy "$@"; }
  claude() { "$HOME/.config/pop-workstation/agent-scope" claude "$@"; }
  codex() { "$HOME/.config/pop-workstation/agent-scope" codex "$@"; }
  copilot() { "$HOME/.config/pop-workstation/agent-scope" copilot "$@"; }
fi
