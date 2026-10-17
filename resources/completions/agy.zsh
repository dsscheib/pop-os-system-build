#compdef agy
# Bundled top-level commands/options; retain filename completion for arguments.
local -a commands options
commands=(agent agents changelog help install mcp mic-serve models plugin plugins remote-control update)
options=(--help --version --add-dir --agent --continue --conversation --disable-slash-commands --effort --input-format --json-schema --log-file --mode --model --new-project --output-format --print --print-timeout --project --prompt --prompt-interactive --remote-control --sandbox)
if [[ $words[CURRENT] == -* ]]; then
  compadd -- "${options[@]}"
elif (( CURRENT == 2 )); then
  compadd -- "${commands[@]}"
fi
_files
