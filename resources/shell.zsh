export PATH="$HOME/.local/bin:${GOBIN:-${GOPATH:-$HOME/go}/bin}:${CARGO_HOME:-$HOME/.cargo}/bin:$PATH"
fpath=("$HOME/.zsh/completion" $fpath)
if command -v mise >/dev/null; then eval "$(mise activate zsh)"; fi
# Interactive agent CLIs run in a task- and memory-limited scope; see agent-scope.
if [[ -o interactive ]]; then
  agy() { "$HOME/.config/pop-workstation/agent-scope" agy "$@"; }
  claude() { "$HOME/.config/pop-workstation/agent-scope" claude "$@"; }
  codex() { "$HOME/.config/pop-workstation/agent-scope" codex "$@"; }
  copilot() { "$HOME/.config/pop-workstation/agent-scope" copilot "$@"; }
fi
