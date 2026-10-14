fish_add_path "$HOME/.local/bin" "$HOME/go/bin" "$HOME/.cargo/bin"
if command -q mise
    mise activate fish | source
end
