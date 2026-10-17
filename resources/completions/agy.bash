# Bundled top-level commands/options; retain filename completion for arguments.
_pop_agy_complete() {
  local current=${COMP_WORDS[COMP_CWORD]} candidates=''
  if [[ $current == -* ]]; then
    candidates='--help --version --add-dir --agent --continue --conversation --disable-slash-commands --effort --input-format --json-schema --log-file --mode --model --new-project --output-format --print --print-timeout --project --prompt --prompt-interactive --remote-control --sandbox'
  elif (( COMP_CWORD == 1 )); then
    candidates='agent agents changelog help install mcp mic-serve models plugin plugins remote-control update'
  fi
  mapfile -t COMPREPLY < <(compgen -W "$candidates" -- "$current")
}
complete -o default -o bashdefault -F _pop_agy_complete agy
