export PATH="$HOME/.local/bin:${GOBIN:-${GOPATH:-$HOME/go}/bin}:${CARGO_HOME:-$HOME/.cargo}/bin:$PATH"
fpath=("$HOME/.zsh/completion" $fpath)
if command -v mise >/dev/null; then eval "$(mise activate zsh)"; fi
