function __pop_django_complete
    set -l words (commandline -opc)
    set -l current (commandline -ct)
    if test (count $words) -eq 0
        return
    end
    set -l executable $words[1]
    if test "$executable" = manage.py; and test -x ./manage.py
        set executable ./manage.py
    end
    set -l words_string (string join ' ' -- $words)
    set -l index (count $words)
    env DJANGO_AUTO_COMPLETE=1 COMP_WORDS="$words_string $current" COMP_CWORD=$index $executable 2>/dev/null
end
complete -c django-admin -f -a '(__pop_django_complete)'
complete -c manage.py -f -a '(__pop_django_complete)'
