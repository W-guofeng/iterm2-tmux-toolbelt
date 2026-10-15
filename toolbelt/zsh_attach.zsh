# Installed only by an explicit user-requested no-echo setup action.
# Optional Toolbelt ZLE bridge. Source after official iTerm2 shell integration.
# Never changes history options, echo permanently, aliases, or existing bindings.
[[ -o interactive && -o zle ]] || return 0
(( ${+functions[iterm2_precmd]} && ${+functions[iterm2_set_user_var]} )) || return 0
[[ -n ${_it2tb_loaded-} ]] && return 0

autoload -Uz add-zsh-hook
# A private escape sequence, not a printable command or Enter. Refuse conflicts.
for _it2tb_map in emacs viins vicmd; do
  _it2tb_bound=$(bindkey -M "$_it2tb_map" $'\e[1337;777~' 2>/dev/null)
  [[ $_it2tb_bound == *' undefined-key' ]] || return 0
done
unset _it2tb_map _it2tb_bound

typeset -g _it2tb_dir=$(command mktemp -d "/tmp/it2tb-${UID}-${$}-XXXXXXXXXX")
[[ -d $_it2tb_dir && ! -L $_it2tb_dir ]] || return 0
typeset -g _it2tb_loaded=1 _it2tb_nonce='' _it2tb_tty=''

_it2tb_ready() {
  emulate -L zsh
  setopt localoptions extendedglob no_aliases
  local _it2tb_new
  _it2tb_new=$(command od -An -N16 -tx1 /dev/urandom | command tr -d ' \n')
  [[ ${#_it2tb_new} == 32 ]] || return 1
  command rm -f "$_it2tb_dir/request"
  _it2tb_nonce=$_it2tb_new
  # Opening /dev/tty reports that special path; tmux refuses it. Zsh's own
  # TTY parameter identifies the real terminal device of this interactive shell.
  _it2tb_tty=$TTY
  [[ $_it2tb_tty == /dev/pts/<-> || $_it2tb_tty == /dev/ttys[[:alnum:]]## || $_it2tb_tty == /dev/tty<-> ]] || return 1
  [[ -c $_it2tb_tty && -O $_it2tb_tty && ! -L $_it2tb_tty ]] || return 1
  # Only protocol identity/one-shot data lives here, never SSH/HTTP credentials.
  local _it2tb_identity
  _it2tb_identity=$(command mktemp "$_it2tb_dir/.identity.XXXXXXXX") || return 1
  print -r -- "$_it2tb_nonce" > "$_it2tb_identity"
  command mv -f "$_it2tb_identity" "$_it2tb_dir/identity" || return 1
  iterm2_set_user_var toolbelt_zsh_history_event "$HISTNO"
  iterm2_set_user_var toolbelt_zsh_display_version "3"
  iterm2_set_user_var toolbelt_zsh_ready "2|$UID|$$|$_it2tb_tty|$_it2tb_nonce|$_it2tb_dir"
}

_it2tb_cleanup() {
  [[ -d $_it2tb_dir && ! -L $_it2tb_dir ]] || return 0
  command rm -f "$_it2tb_dir/request" "$_it2tb_dir/identity"
  command rmdir "$_it2tb_dir" 2>/dev/null
}

_it2tb_report() {
  iterm2_set_user_var toolbelt_zsh_result "$_it2tb_nonce|$1"
}

_it2tb_attach_widget() {
  emulate -L zsh
  setopt localoptions extendedglob no_aliases
  local _it2tb_request="$_it2tb_dir/request"
  local -a _it2tb_fields
  local _it2tb_age _it2tb_now _it2tb_saved _it2tb_rc=1 _it2tb_guard _it2tb_actual
  local _it2tb_socket _it2tb_sid _it2tb_pid _it2tb_created _it2tb_received
  if [[ ! -f $_it2tb_request || -L $_it2tb_request || ! -O $_it2tb_request ]]; then
    _it2tb_report no-request
    return 1
  fi
  IFS=$'\t' read -rA _it2tb_fields < "$_it2tb_request"
  _it2tb_age=$(command stat -c %Y "$_it2tb_request" 2>/dev/null) || _it2tb_age=$(command stat -f %m "$_it2tb_request" 2>/dev/null)
  command rm -f "$_it2tb_request"
  # Consume before checking BUFFER: a refused request cannot replay later.
  if [[ -n $BUFFER || ${#_it2tb_fields} != 5 ]]; then
    _it2tb_report busy-or-invalid
    zle -M 'Toolbelt: input is not empty; attachment cancelled'
    return 1
  fi
  _it2tb_now=$(command date +%s)
  if [[ $_it2tb_age != [0-9]## ]] || (( _it2tb_now - _it2tb_age > 15 || _it2tb_age > _it2tb_now + 2 )); then
    _it2tb_report expired
    zle -M 'Toolbelt: attachment request expired'
    return 1
  fi
  _it2tb_socket=$_it2tb_fields[1]; _it2tb_sid=$_it2tb_fields[2]
  _it2tb_pid=$_it2tb_fields[3]; _it2tb_created=$_it2tb_fields[4]; _it2tb_received=$_it2tb_fields[5]
  if [[ $_it2tb_received != $_it2tb_nonce || $_it2tb_socket != /* || $_it2tb_socket == *[[:cntrl:]]* ||
        $_it2tb_sid != \$[0-9]## || $_it2tb_pid != [0-9]## || $_it2tb_created != [0-9]## ]]; then
    _it2tb_report stale-or-invalid
    zle -M 'Toolbelt: stale or invalid attachment target'
    return 1
  fi
  _it2tb_actual=$(command tmux -u -S "$_it2tb_socket" display-message -p -t "$_it2tb_sid" '#{pid}|#{session_id}|#{session_created}' 2>/dev/null)
  if [[ $_it2tb_actual != "$_it2tb_pid|$_it2tb_sid|$_it2tb_created" ]]; then
    _it2tb_report stale-target
    zle -M 'Toolbelt: target changed; attachment cancelled'
    return 1
  fi
  _it2tb_guard='#{&&:#{==:#{pid},'$_it2tb_pid'},#{&&:#{==:#{session_id},'$_it2tb_sid'},#{==:#{session_created},'$_it2tb_created'}}}'
  _it2tb_saved=$(command stty -g < /dev/tty) || return 1
  # Standard ZLE suspension/repaint only; no clear and no scrollback erasure.
  zle -I
  iterm2_preexec
  iterm2_set_user_var toolbelt_tmux_tty "$_it2tb_nonce:$_it2tb_tty"
  {
    # Both tmux stdin and stdout must remain attached to this exact real TTY.
    # tmux passes stdin to its server as the terminal fd and writes its display
    # through that fd. Open fd 0 read/write: a read-only < redirection attaches
    # successfully but silently prevents all tmux screen output.
    # Do not capture/filter output; normal tmux notices remain visible.
    command tmux -u -S "$_it2tb_socket" if-shell -F -t "$_it2tb_sid" "$_it2tb_guard" \
      "attach-session -t '$_it2tb_sid'" "display-message -p 'Toolbelt: target changed; attachment cancelled'" \
      <> "$_it2tb_tty" > "$_it2tb_tty" 2> "$_it2tb_tty"
    _it2tb_rc=$?
  } always {
    command stty "$_it2tb_saved" < /dev/tty 2>/dev/null
    if (( _it2tb_rc == 0 )); then _it2tb_report returned; else _it2tb_report attach-failed; fi
    # "returned" is not success proof; the caller must confirm its exact tmux
    # client via the independent query channel. A widget is not an accepted command line: explicitly finish the semantic
    # command and publish a fresh prompt/nonce, without adding history entries.
    iterm2_precmd
    _it2tb_ready
    zle reset-prompt
  }
  return $_it2tb_rc
}

zle -N _it2tb_attach_widget
for _it2tb_map in emacs viins vicmd; do
  bindkey -M "$_it2tb_map" $'\e[1337;777~' _it2tb_attach_widget
done
unset _it2tb_map
add-zsh-hook precmd _it2tb_ready
add-zsh-hook zshexit _it2tb_cleanup
