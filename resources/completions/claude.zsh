#compdef claude
# Bundled top-level commands/options; retain filename completion for arguments.
local -a commands options
commands=(agents attach auth auto-mode doctor gateway import install logs mcp plugin purge respawn rm setup-token stop ultrareview update)
options=(--help --version --add-dir --agent --agents --allowedTools --append-system-prompt --continue --debug --effort --fork-session --ide --input-format --json-schema --max-budget-usd --mcp-config --model --name --output-format --permission-mode --plugin-dir --print --resume --session-id --settings --strict-mcp-config --system-prompt --tools --verbose --worktree)
if [[ $words[CURRENT] == -* ]]; then
  compadd -- "${options[@]}"
elif (( CURRENT == 2 )); then
  compadd -- "${commands[@]}"
fi
_files
