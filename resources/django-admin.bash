_pop_django_complete() {
  local executable=$1
  if [[ $executable == manage.py && -x ./manage.py ]]; then executable=./manage.py; fi
  mapfile -t COMPREPLY < <(env DJANGO_AUTO_COMPLETE=1 COMP_WORDS="${COMP_WORDS[*]}" COMP_CWORD="$COMP_CWORD" "$executable" 2>/dev/null)
}
complete -F _pop_django_complete -o default django-admin manage.py
