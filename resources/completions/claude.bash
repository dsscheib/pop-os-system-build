# Bundled top-level commands/options; retain filename completion for arguments.
_pop_claude_complete() {
  local current=${COMP_WORDS[COMP_CWORD]} candidates=''
  if [[ $current == -* ]]; then
    candidates='--help --version --add-dir --agent --agents --allowedTools --append-system-prompt --continue --debug --effort --fork-session --ide --input-format --json-schema --max-budget-usd --mcp-config --model --name --output-format --permission-mode --plugin-dir --print --resume --session-id --settings --strict-mcp-config --system-prompt --tools --verbose --worktree'
  elif (( COMP_CWORD == 1 )); then
    candidates='agents attach auth auto-mode doctor gateway import install logs mcp plugin purge respawn rm setup-token stop ultrareview update'
  fi
  mapfile -t COMPREPLY < <(compgen -W "$candidates" -- "$current")
}
complete -o default -o bashdefault -F _pop_claude_complete claude
