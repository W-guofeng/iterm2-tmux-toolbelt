# One explicit sidebar action. No sudo, download, daemon or terminal input.
set -eu
umask 077
test "$(hostname)" = "$expected_host" && test "$(id -un)" = "$expected_user" || exit 31
case "$HOME" in *'
'*|*'	'*) exit 32;; esac
uid=$(id -u)
owner() { stat -c %u "$1" 2>/dev/null || stat -f %u "$1" 2>/dev/null; }
test -d "$HOME" && test ! -L "$HOME" && test "$(owner "$HOME")" = "$uid" || exit 32
record=$(getent passwd "$uid") || exit 33
login_shell=$(printf '%s' "$record" | cut -d: -f7)
case "$login_shell:$flavor" in */zsh:zsh|*/bash:bash) ;; *) exit 33;; esac
interpreter=$(command -v "$flavor") || exit 33
case "$interpreter" in /*) ;; *) exit 33;; esac
dest="$HOME/.iterm2_shell_integration.$flavor"
line="[[ -r \"\$HOME/.iterm2_shell_integration.$flavor\" ]] && source \"\$HOME/.iterm2_shell_integration.$flavor\""
if test "$flavor" = bash; then
    # .profile may also be read by POSIX shells; load bash code only in bash.
    line='if [ -n "${BASH_VERSION-}" ] && [ -r "$HOME/.iterm2_shell_integration.bash" ]; then . "$HOME/.iterm2_shell_integration.bash"; fi'
fi
if test "${install_kind:-official}" = noecho; then
    test "$flavor" = zsh || exit 33
    test -f "$HOME/.iterm2_shell_integration.zsh" && test ! -L "$HOME/.iterm2_shell_integration.zsh" || exit 37
    test -f "$HOME/.zshrc" && grep -qF '.iterm2_shell_integration.zsh' "$HOME/.zshrc" || exit 37
    dest="$HOME/.iterm2_toolbelt_attach.zsh"
    line='[[ -r "$HOME/.iterm2_toolbelt_attach.zsh" ]] && source "$HOME/.iterm2_toolbelt_attach.zsh"'
    reference=.iterm2_toolbelt_attach.zsh
    backup_label=toolbelt-noecho-backup
else
    reference=".iterm2_shell_integration.$flavor"
    backup_label=toolbelt-backup
fi
hash() {
    if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
    else shasum -a 256 "$1" | cut -d' ' -f1; fi
}
safe_file() {
    test ! -L "$1" || return 1
    if test -e "$1"; then
        test -f "$1" && test "$(owner "$1")" = "$uid" && test "$(wc -c < "$1")" -le 1048576
    fi
}
mkdir "$HOME/.iterm2-toolbelt-setup.lock" 2>/dev/null || exit 34
work=
complete=0
new_script=0
replace_script=0
cleanup() {
    if test -n "$work" && test "$complete" != 1; then
        if test -f "$work/changed"; then
            while IFS= read -r name; do
                if cmp -s "$HOME/$name" "$work/$name.installed"; then
                    if test -f "$work/$name.original"; then
                        cp -p "$work/$name.original" "$work/restore" && mv "$work/restore" "$HOME/$name"
                    else rm -f "$HOME/$name"; fi
                fi
            done < "$work/changed"
        fi
        if test "$replace_script" = 1 && cmp -s "$dest" "$work/integration"; then
            cp -p "$work/helper.original" "$work/helper.restore" && mv "$work/helper.restore" "$dest"
        elif test "$new_script" = 1 && cmp -s "$dest" "$work/integration"; then rm -f "$dest"; fi
    fi
    if test -n "$work"; then rm -rf "$work"; fi
    rmdir "$HOME/.iterm2-toolbelt-setup.lock" 2>/dev/null || :
}
trap cleanup EXIT
trap 'exit 35' HUP INT TERM
work=$(mktemp -d "$HOME/.iterm2-toolbelt-setup.XXXXXXXX") || exit 35
printf '%s' "$script_b64" | base64 -d > "$work/integration" || exit 36
test "$(hash "$work/integration")" = "$script_sha" || exit 36
"$interpreter" -n "$work/integration" || exit 36
safe_file "$dest" || exit 32
if test -e "$dest" && test "$(hash "$dest")" != "$script_sha"; then
    test "${install_kind:-official}" = noecho && test "$(hash "$dest")" = "$previous_sha" || exit 37
    cp -p "$dest" "$work/helper.original"
fi
if test "$flavor" = zsh; then
    names=.zshrc
else
    login=.bash_profile
    for candidate in .bash_profile .bash_login .profile; do
        if test -e "$HOME/$candidate" || test -L "$HOME/$candidate"; then login=$candidate; break; fi
    done
    names=".bashrc $login"
fi
: > "$work/pending"
: > "$work/changed"
: > "$work/receipt"
for name in $names; do
    path="$HOME/$name"
    safe_file "$path" || exit 32
    if test -e "$path"; then
        # An existing customized integration hook is left to its owner.
        if grep -qF "$reference" "$path"; then
            grep -qxF "$line" "$path" || exit 37
            continue
        fi
        cp -p "$path" "$work/$name.original"
        cp -p "$path" "$work/$name.new"
    else : > "$work/$name.new"; fi
    printf '\n# tmux Toolbelt explicit shell setup\n%s\n' "$line" >> "$work/$name.new"
    "$interpreter" -n "$work/$name.new" || exit 38
    cp -p "$work/$name.new" "$work/$name.installed"
    printf '%s\n' "$name" >> "$work/pending"
done
# Prepare every backup before changing any startup file.
stamp=$(date -u +%Y%m%dT%H%M%SZ)
while IFS= read -r name; do
    if test -f "$work/$name.original"; then
        backup=$(mktemp "$HOME/$name.$backup_label-$stamp-XXXXXXXX") || exit 35
        cp -p "$work/$name.original" "$backup" || exit 35
        if test "${install_kind:-official}" = noecho; then chmod 600 "$backup" || exit 35; fi
        printf 'backup\t%s\n' "$backup" >> "$work/receipt"
    fi
done < "$work/pending"
if test -f "$work/helper.original"; then
    backup=$(mktemp "$dest.toolbelt-backup-$stamp-XXXXXXXX") || exit 35
    cp -p "$work/helper.original" "$backup" && chmod 600 "$backup" || exit 35
    printf 'backup\t%s\n' "$backup" >> "$work/receipt"
    safe_file "$dest" && cmp -s "$dest" "$work/helper.original" || exit 39
    cp "$work/integration" "$work/helper.replacement" && chmod 600 "$work/helper.replacement" || exit 35
    replace_script=1
    mv "$work/helper.replacement" "$dest" || exit 35
elif test ! -e "$dest"; then
    # Hard link is atomic and cannot overwrite another file.
    ln "$work/integration" "$dest" || exit 35
    new_script=1
fi
while IFS= read -r name; do
    path="$HOME/$name"
    safe_file "$path" || exit 32
    if test -f "$work/$name.original"; then
        cmp -s "$path" "$work/$name.original" || exit 39
    else test ! -e "$path" || exit 39; fi
    printf '%s\n' "$name" >> "$work/changed"
    mv "$work/$name.new" "$path" || exit 35
done < "$work/pending"
complete=1
printf 'ok\n'
cat "$work/receipt"
