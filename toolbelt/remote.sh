# Runs inside one private SSH channel. No eval, remote files, or Python needed.
unset TMUX
LC_ALL=C
export LC_ALL
uid=$(id -u) || exit 1
user=$(id -un) || exit 1
host=$(hostname) || exit 1
bin=$(command -v tmux) || bin=
nonce=$1
reply() {
    printf '%s\t%s\t' "$nonce" "$1"
    printf '%s' "$2" | base64 | tr -d '\r\n'
    printf '\n'
}
valid_socket() {
    case "$socket" in /*) ;; *) return 1;; esac
    test ! -L "$socket" || return 1
    if test -e "$socket"; then
        test -S "$socket" || return 1
        owner=$(stat -c %u "$socket" 2>/dev/null) || owner=$(stat -f %u "$socket" 2>/dev/null) || return 1
        test "$owner" = "$uid" || return 1
    fi
}
server_pid() {
    info=$("$bin" -u -S "$socket" display-message -p '#{pid}' 2>&1)
    if test $? = 0; then printf '%s' "$info"; return 0; fi
    case "$info" in
        *'Connection refused)'|*'No such file or directory)'|'no server running on '*) return 0;;
        *) return 13;;
    esac
}
client_row() {
    test "$tty" != '-' || return 0
    "$bin" -u -S "$socket" list-clients -F '#{client_tty}|#{client_pid}|#{session_id}' 2>/dev/null |
        while IFS='|' read -r path pid sid; do
            if test "$path" = "$tty"; then printf '%s\t%s\t%s\n' "$path" "$pid" "$sid"; fi
        done
}
bridge_dir() {
    case "$inbox" in "/tmp/it2tb-$uid-$clientpid-"*) ;; *) return 1;; esac
    suffix=${inbox##*/it2tb-$uid-$clientpid-}
    case "$suffix" in ''|*[!A-Za-z0-9]*) return 1;; esac
    test ${#suffix} -ge 8 && test ${#suffix} -le 32 || return 1
    test -d "$inbox" && test ! -L "$inbox" || return 1
    info=$(stat -c '%u %a' "$inbox" 2>/dev/null) || info=$(stat -f '%u %Lp' "$inbox") || return 1
    test "$info" = "$uid 700" || return 1
}
bridge_record() { printf '%s\t%s\t%s\t%s\t%s\n' "$socket" "$sid" "$expected" "$created" "$name"; }
handle() {
    if test "$op" = cancel-attach; then
        bridge_dir || return 30
        test ! -L "$inbox/request" || return 30
        if test -f "$inbox/request" && test "$(cat "$inbox/request")" = "$(bridge_record)"; then
            rm -f "$inbox/request" || return 30
        fi
        return 0
    fi
    case "$op" in
    setup)
        record=$(getent passwd "$uid" 2>/dev/null) || record=
        login_shell=$(printf '%s' "$record" | cut -d: -f7)
        printf '%s\n' "$login_shell"
        for file in .bashrc .bash_profile .bash_login .profile .zshrc .iterm2_shell_integration.bash .iterm2_shell_integration.zsh .iterm2_toolbelt_attach.zsh; do
            present=0; reference=0
            if test -f "$HOME/$file"; then present=1; fi
            case "$file" in .iterm2_toolbelt_attach.zsh)
                if test -r "$HOME/.zshrc" && grep -qF '.iterm2_toolbelt_attach.zsh' "$HOME/.zshrc"; then reference=1; fi;;
                .iterm2*) ;; *)
                if test -r "$HOME/$file" && grep -qF '.iterm2_shell_integration' "$HOME/$file"; then reference=1; fi;;
            esac
            printf '%s|%s|%s\n' "$file" "$present" "$reference"
        done
        current_helper=0
        if test -f "$HOME/.iterm2_toolbelt_attach.zsh" && grep -qF 'toolbelt_zsh_display_version "3"' "$HOME/.iterm2_toolbelt_attach.zsh"; then current_helper=1; fi
        printf 'helper-display|%s|0\n' "$current_helper"
        return 0;;
    hello)
        test -n "$bin" || return 20
        printf '%s\t%s\t%s\t%s\n' "$host" "$user" "$uid" "${TMUX_TMPDIR:-/tmp}/tmux-$uid/default"
        for root in /tmp "${TMUX_TMPDIR:-/tmp}"; do
            for path in "$root/tmux-$uid/"*; do
                if test -S "$path" && test ! -L "$path"; then printf '%s\n' "$path"; fi
            done
        done
        return 0;;
    esac
    valid_socket || return 12
    actual=$(server_pid) || return 13
    case "$op" in
    inspect)
        if test -z "$actual"; then
            printf '%s\t%s\tabsent\t-\t-\t-\n' "$host" "$socket"
            return 0
        fi
        client=$(client_row) || return 13
        if test -z "$client"; then client=$(printf '%s\t%s\t%s' '-' '-' '-'); fi
        printf '%s\t%s\t%s\t%s\n' "$host" "$socket" "$actual" "$client"
        "$bin" -u -S "$socket" list-sessions -F '#{session_id}|#{session_created}|#{session_windows}|#{session_attached}|#{session_name}'
        return $?;;
    create|switch|rename|delete|detach|prepare-attach) ;;
    *) return 14;;
    esac
    if test "$expected" = absent; then
        test -z "$actual" && test "$op" = create || return 15
    else
        test "$actual" = "$expected" || return 15
    fi
    if test "$op" != create && test "$op" != detach; then
        actual_created=$("$bin" -u -S "$socket" display-message -p -t "$sid" '#{session_id}|#{session_created}' 2>/dev/null) || return 15
        test "$actual_created" = "$sid|$created" || return 15
    fi
    if test "$op" = prepare-attach; then
        bridge_dir || return 30
        case "$name" in *[!0-9a-f]*) return 30;; esac
        test ${#name} -eq 32 || return 30
        test -f "$inbox/identity" && test ! -L "$inbox/identity" || return 30
        info=$(stat -c '%u %a' "$inbox/identity" 2>/dev/null) || info=$(stat -f '%u %Lp' "$inbox/identity") || return 30
        test "$info" = "$uid 600" && test "$(cat "$inbox/identity")" = "$name" || return 30
        shell_uid=$(ps -p "$clientpid" -o uid= | tr -d '[:space:]') || return 30
        shell_tty=$(ps -p "$clientpid" -o tty= | tr -d '[:space:]') || return 30
        test "$shell_uid" = "$uid" && test "$shell_tty" = "${tty#/dev/}" || return 30
        test -c "$tty" && test ! -L "$tty" || return 30
        temp=$(umask 077; mktemp "$inbox/.request.XXXXXXXX") || return 30
        bridge_record > "$temp" || { rm -f "$temp"; return 30; }
        ln "$temp" "$inbox/request" 2>/dev/null || { rm -f "$temp"; return 30; }
        rm -f "$temp"
        return 0
    fi
    if test "$op" = switch || test "$op" = detach; then
        client=$(client_row)
        path=$(printf '%s' "$client" | cut -f1)
        pid=$(printf '%s' "$client" | cut -f2)
        test "$path" = "$tty" && test "$pid" = "$clientpid" && test "$tty" != '-' || return 15
    fi
    if test "$expected" = absent; then
        parent=${socket%/*}
        for root in /tmp "${TMUX_TMPDIR:-/tmp}"; do
            if test "$parent" = "$root/tmux-$uid" && test ! -L "$parent"; then
                (umask 077; mkdir -p "$parent") || return 12
            fi
        done
        "$bin" -u -S "$socket" new-session -d -s "$name" -P -F '#{session_id}'
        return $?
    fi
    if test "$op" = detach; then
        current=$(client_row | cut -f3)
        policy=$("$bin" -u -S "$socket" display-message -p -t "$current" '#{destroy-unattached}' 2>/dev/null) || return 21
        test "$policy" = off || return 21
    fi
    guard="#{==:#{pid},$expected}"
    if test "$op" != create && test "$op" != detach; then
        guard="#{&&:$guard,#{&&:#{==:#{session_id},$sid},#{==:#{session_created},$created}}}"
    fi
    client_guard="#{!!:#{L:#{?#{&&:#{==:#{client_tty},$tty},#{==:#{client_pid},$clientpid}},1,}}}"
    if test "$op" = switch || test "$op" = detach; then guard="#{&&:$guard,$client_guard}"; fi
    case "$op" in
        create) command="new-session -d -s '$name' -P -F '#{session_id}'";;
        switch) command="switch-client -c '$tty' -t '$sid'";;
        rename) command="rename-session -t '$sid' '$name'";;
        detach) command="detach-client -t '$tty'";;
        delete)
            command="kill-session -t '$sid'"
            client=$(client_row)
            pid=$(printf '%s' "$client" | cut -f2)
            current=$(printf '%s' "$client" | cut -f3)
            if test "$current" = "$sid" && test "$pid" = "$clientpid"; then
                fallback=$("$bin" -u -S "$socket" list-sessions -F '#{session_id}' | while read -r other; do
                    if test "$other" != "$sid"; then printf '%s\n' "$other"; break; fi
                done)
                if test -n "$fallback"; then
                    command="if-shell -F '$client_guard' { switch-client -c '$tty' -t '$fallback' } ; $command"
                fi
            fi;;
    esac
    # -F is a tmux format, never an OS shell condition. The guard and action
    # execute on the same tmux server command queue, even across socket races.
    stale="$nonce:stale"
    if test "$op" = detach; then
        guard="#{&&:$guard,#{==:#{destroy-unattached},off}}"
        sid=$current
    fi
    if test "$op" = create; then
        value=$("$bin" -u -S "$socket" if-shell -F "$guard" "$command" "display-message -p '$stale'") || return 15
    else
        value=$("$bin" -u -S "$socket" if-shell -F -t "$sid" "$guard" "$command" "display-message -p '$stale'") || return 15
    fi
    test "$value" != "$stale" || return 15
    printf '%s' "$value"

}
while IFS="$(printf '\t')" read -r op socket sid name expected created tty clientpid inbox; do
    # Host-side validates every field too; these guard the shell protocol itself.
    test ${#socket} -le 4096 && test ${#name} -le 256 && test ${#inbox} -le 256 || exit 1
    result=$(handle 2>/dev/null)
    status=$?
    reply "$status" "$result"
done
