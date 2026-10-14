export PATH="$HOME/.local/bin:${GOBIN:-${GOPATH:-$HOME/go}/bin}:${CARGO_HOME:-$HOME/.cargo}/bin:$PATH"
# Load programmable completion (including APT) before other shell integrations.
if [[ $- == *i* && -z ${BASH_COMPLETION_VERSINFO:-} && -r /usr/share/bash-completion/bash_completion ]]; then
  # shellcheck source=/dev/null
  source /usr/share/bash-completion/bash_completion
fi
if command -v mise >/dev/null; then eval "$(mise activate bash)"; fi
