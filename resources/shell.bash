export PATH="$HOME/.local/bin:${GOBIN:-${GOPATH:-$HOME/go}/bin}:${CARGO_HOME:-$HOME/.cargo}/bin:$PATH"
if command -v mise >/dev/null; then eval "$(mise activate bash)"; fi
