fish_add_path "$HOME/.local/bin" "$HOME/go/bin" "$HOME/.cargo/bin"
if command -q mise
    mise activate fish | source
end
# Interactive agent CLIs run in a task- and memory-limited scope; see agent-scope.
if status is-interactive
    for agent in agy claude codex copilot
        function $agent --inherit-variable agent
            "$HOME/.config/pop-workstation/agent-scope" $agent $argv
        end
    end
    set --erase agent
end
