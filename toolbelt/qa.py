"""Explicit opt-in real iTerm2/SSH QA, fenced to newly created disposable tabs.

Reads its destination/profile from private stdin JSON. It never targets an
existing tab or production tmux socket. Native WebKit drives the real plugin
HTTP/UI/SDK path with exact pane/socket/server guards before every click.
"""
import asyncio
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import urllib.request
import urllib.error
from .process import ForegroundSSH, parse_ssh_argv
from .core import ToolbeltError


def registered_url():
    raw=subprocess.check_output(['defaults','read','com.googlecode.iterm2','NoSyncDynamicTools'],text=True)
    match=re.search(r'"dev\.iterm2-tmux-toolbelt\.sessions"\s*=\s*\{\s*URL\s*=\s*"http://127\.0\.0\.1:([0-9]+)/#([A-Za-z0-9_-]+)"',raw)
    if not match: raise RuntimeError('Plugin is not registered')
    return 'http://127.0.0.1:'+match[1]+'/#'+match[2]


async def http(url,path,payload=None):
    origin,token=url.split('/#')
    def request():
        headers={'Authorization':'Bearer '+token}
        if payload is not None: headers.update({'Origin':origin,'Content-Type':'application/json'})
        req=urllib.request.Request(origin+path,headers=headers,data=json.dumps(payload).encode() if payload is not None else None)
        try:
            with urllib.request.urlopen(req,timeout=25) as response: return json.load(response)
        except urllib.error.HTTPError as exc:
            result=json.load(exc)
            raise RuntimeError(result.get('error','QA HTTP request failed')) from None
    return await asyncio.to_thread(request)


async def remote(target,script):
    parse_ssh_argv(['ssh',target])
    proc=await asyncio.create_subprocess_exec('/usr/bin/ssh','-oBatchMode=yes','-oStrictHostKeyChecking=yes',
        '-oConnectTimeout=10','-oPermitLocalCommand=no','-oRemoteCommand=none','-oClearAllForwardings=yes',
        '-oForwardAgent=no',target,'sh -c '+shlex.quote(script),stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    try: output,error=await asyncio.wait_for(proc.communicate(),25)
    except BaseException:
        if proc.returncode is None: proc.terminate()
        await proc.wait()
        raise
    if proc.returncode: raise RuntimeError('QA-only SSH command failed; no credentials/settings changed')
    return output.decode().strip()


async def history_signatures(target):
    # Aggregate only our exact legacy payload/test signatures; no history text
    # or credentials leave the remote host. HISTNO can change on history import.
    script="""set -eu
files=0; commands=0; markers=0
for file in "$HOME/.zsh_history" "$HOME/.histfile"; do
 if [ -f "$file" ] && [ ! -L "$file" ] && [ -r "$file" ]; then
  files=$((files+1))
  value=$(grep -cF 'sh -c "$(printf %s ' "$file" || :)
  commands=$((commands+value))
  value=$(grep -cF '__TOOLBELT_QA_UNFINISHED__' "$file" || :)
  markers=$((markers+value))
 fi
done
printf '%s %s %s\\n' "$files" "$commands" "$markers"
"""
    result=tuple(map(int,(await remote(target,script)).split()))
    if len(result)!=3: raise RuntimeError('History signature count response invalid')
    return result


async def check_noecho(api, connection, app, pane, url, socket, pid, uid, target):
    """Faults only in the exact new QA pane and private fixture socket."""
    from .bridge import KEY, parse_ready

    async def focused():
        await app.async_refresh()
        window=app.current_terminal_window
        current=window.current_tab.current_session if window and window.current_tab else None
        if not current or current.session_id!=pane.session_id:
            raise RuntimeError('QA focus changed; no terminal input sent')

    async def idle(prior=None):
        for _ in range(60):
            await focused()
            prompt=await api.async_get_last_prompt(connection,pane.session_id)
            value=await pane.async_get_variable('user.toolbelt_zsh_ready')
            try: bridge=parse_ready(value,uid)
            except ToolbeltError: bridge=None
            if bridge and (prior is None or bridge.nonce!=prior) and prompt and prompt.state==api.PromptState.EDITING and not prompt.command:
                return bridge
            await asyncio.sleep(.1)
        raise RuntimeError('QA terminal did not return to an empty verified prompt')

    async def send(text):
        async with api.Transaction(connection):
            await focused()
            await pane.async_send_text(text,suppress_broadcast=True)

    async def action(kind,name=None):
        await focused()
        state=await http(url,'/api/state')
        scope=state.get('target') or {}
        if scope.get('pane')!=pane.session_id or scope.get('socket')!=socket or scope.get('server_pid')!=pid:
            raise RuntimeError('No-echo QA action scope changed')
        payload={'action':kind,'context':state['context']}
        if name:
            row=next(r for r in state['sessions'] if r['name']==name)
            payload.update(id=row['id'],created=row['created'])
        return await http(url,'/api/action',payload)

    bridge=await idle()
    marker='__TOOLBELT_QA_UNFINISHED__'
    await send(marker)
    for _ in range(40):
        prompt=await api.async_get_last_prompt(connection,pane.session_id)
        if prompt and prompt.command==marker: break
        await asyncio.sleep(.1)
    else: raise RuntimeError('QA partial input did not reach its own terminal')
    try:
        await action('switch','qa-base')
    except RuntimeError:
        pass
    else: raise RuntimeError('Busy/partial QA prompt unexpectedly accepted attachment')
    prompt=await api.async_get_last_prompt(connection,pane.session_id)
    if not prompt or prompt.command!=marker: raise RuntimeError('Partial input was altered')
    await send('\x03')
    bridge=await idle(bridge.nonce)
    # Deliberately stale producer data, only in this QA shell's private inbox.
    state=await http(url,'/api/state')
    row=next(r for r in state['sessions'] if r['name']=='qa-base')
    record='\t'.join([socket,row['id'],'999999999',str(row['created']),bridge.nonce])+'\n'
    script='''set -eu
inbox=INBOX
[ -d "$inbox" ] && [ ! -L "$inbox" ]
[ "$(cat "$inbox/identity")" = NONCE ]
temp=$(umask 077; mktemp "$inbox/.request.XXXXXXXX")
printf %s RECORD > "$temp"
ln "$temp" "$inbox/request"
rm "$temp"
'''.replace('INBOX',shlex.quote(bridge.inbox)).replace('NONCE',shlex.quote(bridge.nonce)).replace('RECORD',shlex.quote(record))
    await remote(target,script)
    await focused()
    prompt=await api.async_get_last_prompt(connection,pane.session_id)
    if not prompt or prompt.state!=api.PromptState.EDITING or prompt.command:
        raise RuntimeError('QA prompt changed before deliberate failure key')
    await send(KEY)
    for _ in range(40):
        result=await pane.async_get_variable('user.toolbelt_zsh_result')
        if result==bridge.nonce+'|stale-target': break
        await asyncio.sleep(.1)
    else: raise RuntimeError('QA deliberate stale target was not refused')
    await send('\x03')
    bridge=await idle(bridge.nonce)
    await action('switch','qa-base')
    await send('\x03')  # Private qa-base traps INT; no user's task is involved.
    await asyncio.sleep(.2)
    await action('switch','qa-two')
    await action('detach')
    bridge=await idle(bridge.nonce)
    await send(marker)
    for _ in range(40):
        prompt=await api.async_get_last_prompt(connection,pane.session_id)
        if prompt and prompt.command==marker: break
        await asyncio.sleep(.1)
    else: raise RuntimeError('QA input did not recover after detach/Ctrl-C')
    await send('\x03')
    await idle(bridge.nonce)
    return {'partial_input_refused_and_preserved':True,'stale_target_refused':True,
            'ctrl_c_inside_tmux':True,'input_restored_after_failures':True}


async def run(api,connection):
    args=json.load(sys.stdin)
    target=args['target']; profile=args.get('profile')
    parse_ssh_argv(['ssh',target])
    if '@' not in target: raise RuntimeError('QA needs an explicit authorized user@host')
    expected_user=target.rsplit('@',1)[0]
    app=await api.async_get_app(connection)
    if args.get('cleanup_prefix'):
        from .bridge import parse_ready
        prefix=args['cleanup_prefix']
        if not re.fullmatch(r'[0-9A-Fa-f-]{8,36}',prefix): raise RuntimeError('Invalid own-QA prefix')
        matches=[pane for window in app.terminal_windows for tab in window.tabs for pane in tab.sessions if pane.session_id.startswith(prefix)]
        if not matches:
            print(json.dumps({'own_qa_cleanup':'already_absent'}),flush=True)
            return
        if len(matches)!=1: raise RuntimeError('QA cleanup prefix is ambiguous')
        pane=matches[0]
        prompt=await api.async_get_last_prompt(connection,pane.session_id)
        if not prompt or prompt.state!=api.PromptState.EDITING or prompt.command:
            raise RuntimeError('Own QA terminal is busy; cleanup refused')
        spec=await ForegroundSSH().identify(pane)
        if spec.destination!=target: raise RuntimeError('Own QA terminal target changed')
        uid=await remote(target,'id -u')
        bridge=parse_ready(await pane.async_get_variable('user.toolbelt_zsh_ready'),uid)
        children=await remote(target,'ps --ppid '+bridge.pid+' -o pid= | wc -l')
        if int(children.strip()): raise RuntimeError('Own QA shell has live children; cleanup refused')
        await pane.async_close(force=True)
        print(json.dumps({'own_qa_cleanup':'closed_idle_test_only'}),flush=True)
        return
    original_window=app.current_terminal_window
    original_pane=original_window.current_tab.current_session if original_window and original_window.current_tab else None
    if not original_window or not original_pane: raise RuntimeError('No existing iTerm2 window')
    original_ids={pane.session_id for window in app.terminal_windows for tab in window.tabs for pane in tab.sessions}
    url=registered_url()
    results=[]
    for variant in ('manual','profile') if profile else ('manual',):
        init='''set -eu
[ "$(id -un)" = EXPECTED_USER ] || exit 1
folder=$(mktemp -d /tmp/tb-iterm-qa-XXXXXXXX)
socket="$folder/server.sock"
tmux -u -S "$socket" -f /dev/null new-session -d -s qa-base "printf '\\033[2J\\033[HIT2TB_SCREEN_BASE\\n'; trap : INT; while :; do sleep 1; done"
tmux -u -S "$socket" set-option -g default-command 'sleep 600'
tmux -u -S "$socket" new-session -d -s qa-two "printf '\\033[2J\\033[HIT2TB_SCREEN_TWO\\n'; trap : INT; while :; do sleep 1; done"
printf '%s\\n' "$socket"
tmux -u -S "$socket" display-message -p '#{pid}'
printf '%s\\n' "$(hostname)"
id -un
id -u
'''.replace('EXPECTED_USER',shlex.quote(expected_user))
        fields=(await remote(target,init)).splitlines()
        if len(fields)!=5: raise RuntimeError('Invalid QA fixture identity')
        socket,pid,host,user,uid=fields
        if not re.fullmatch(r'/tmp/tb-iterm-qa-[A-Za-z0-9]+/server.sock',socket) or not pid.isdigit() or user!=expected_user:
            raise RuntimeError('Refusing invalid fixture scope')
        tab=pane=None; success=False
        before_windows={window.window_id for window in app.terminal_windows}
        print(json.dumps({'qa_stage':'create_fenced_terminal','variant':variant}),flush=True)
        try:
            if variant=='manual':
                command=shlex.join(['/usr/bin/ssh','-oBatchMode=yes','-oStrictHostKeyChecking=yes','-oConnectTimeout=15',target])
                tab=await original_window.async_create_tab(command=command)
            else:
                tab=await original_window.async_create_tab(profile=profile)
            if not tab or len(tab.sessions)!=1: raise RuntimeError('QA tab creation failed')
            pane=tab.sessions[0]
            if pane.session_id in original_ids: raise RuntimeError('Refusing existing terminal')
            await pane.async_set_name('Toolbelt QA — '+variant)
            await pane.async_activate(select_tab=True,order_window_front=False)
            detector=ForegroundSSH(); spec=None; metadata={}
            for _ in range(80):
                await app.async_refresh()
                found=app.get_session_by_id(pane.session_id)
                if not found: raise RuntimeError('QA terminal exited before ready')
                actual_host=await found.async_get_variable('hostname')
                actual_user=await found.async_get_variable('username')
                prompt=await api.async_get_last_prompt(connection,pane.session_id)
                metadata={'host_match':isinstance(actual_host,str) and actual_host.rstrip('.').lower()==host.rstrip('.').lower(),
                          'user_match':actual_user==user,'prompt_editing':bool(prompt and prompt.state==api.PromptState.EDITING),
                          'input_empty':bool(prompt and not prompt.command),
                          'helper_ready':str(await found.async_get_variable('user.toolbelt_zsh_ready')).startswith('2|'),
                          'ssh_integration_level':await found.async_get_variable('sshIntegrationLevel')}
                if all(metadata[key] for key in ('host_match','user_match','prompt_editing','input_empty','helper_ready')):
                    spec=await detector.identify(found)
                    if spec.destination!=target: raise RuntimeError('QA Profile target differs from authorized SSH')
                    break
                await asyncio.sleep(.5)
            if spec is None: raise RuntimeError('New QA terminal has no verified idle remote integration: '+json.dumps(metadata))
            print(json.dumps({'qa_stage':'real_sdk_metadata_ready','variant':variant,**metadata}),flush=True)
            for _ in range(30):
                state=await http(url,'/api/state')
                if (state.get('target') or {}).get('pane')==pane.session_id and state['status']=='online': break
                await asyncio.sleep(.5)
            else: raise RuntimeError('Plugin did not resolve isolated QA pane')
            await http(url,'/api/action',{'action':'socket','context':state['context'],'socket':socket})
            state=await http(url,'/api/state')
            if state['target']['pane']!=pane.session_id or state['target']['socket']!=socket or state['target']['server_pid']!=pid:
                raise RuntimeError('Refusing UI actions outside exact fixture pane/socket/server')
            history_before=await history_signatures(target)
            initial_info=await pane.async_get_line_info()
            initial_lines=await pane.async_get_contents(initial_info.overflow,min(300,initial_info.scrollback_buffer_height+initial_info.mutable_area_height))
            initial_nonempty=[line.string for line in initial_lines if line.string.strip()]
            prior_content=initial_nonempty[:-1][:10]
            child=await asyncio.create_subprocess_exec(args.get('webkit_binary','/tmp/toolbelt-webkit-smoke'),
                stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
            payload={'url':url,'width':240,'appearance':'dark','crud':True,'roundtrip':True,
                     'expectedPane':pane.session_id,'expectedSocket':socket,'expectedServer':pid}
            ui_task=asyncio.create_task(child.communicate(json.dumps(payload).encode()))
            visible_sessions=set()
            async def observe_test_screen():
                # Read only this newly created disposable pane. Session metadata
                # alone missed the read-only terminal-fd regression.
                while not ui_task.done():
                    screen=await pane.async_get_screen_contents()
                    text='\n'.join(screen.line(i).string for i in range(screen.number_of_lines))
                    for marker in ('BASE','TWO'):
                        if 'IT2TB_SCREEN_'+marker in text: visible_sessions.add(marker)
                    await asyncio.sleep(.08)
            observer=asyncio.create_task(observe_test_screen())
            try:
                output,_=await asyncio.wait_for(ui_task,50)
                await observer
            finally:
                if not observer.done(): observer.cancel()
                if child.returncode is None:
                    child.terminate()
                    await child.wait()
            ui=json.loads(output)
            if child.returncode or not ui.get('passed') or not ui.get('roundtrip'):
                raise RuntimeError('Fenced native WebKit UI test failed: '+json.dumps(ui))
            extra=await check_noecho(api,connection,app,pane,url,socket,pid,uid,target)
            history_after=await history_signatures(target)
            final_info=await pane.async_get_line_info()
            final_lines=await pane.async_get_contents(final_info.overflow,min(500,final_info.scrollback_buffer_height+final_info.mutable_area_height))
            final_text='\n'.join(line.string for line in final_lines)
            no_long_command=all(text not in final_text for text in ('sh -c "$(printf %s','base64 -d','if-shell -F -t'))
            prior_content_preserved=all(text in final_text for text in prior_content)
            await app.async_refresh()
            found=app.get_session_by_id(pane.session_id)
            after=await detector.identify(found)
            prompt=await api.async_get_last_prompt(connection,pane.session_id)
            state=await http(url,'/api/state')
            checks={'both_session_screens_visible':visible_sessions=={'BASE','TWO'},
                    'no_long_attach_command_visible':no_long_command,
                    'prior_content_preserved':prior_content_preserved,
                    'standard_history_signatures_unchanged':history_before[0]>0 and history_before==history_after,
                    'aborted_input_not_saved':history_after[2]==0,**extra,
                    'same_ssh_process':after.key==spec.key,
                    'same_pane':app.current_terminal_window.current_tab.current_session.session_id==pane.session_id,
                    'no_extra_window':{w.window_id for w in app.terminal_windows}==before_windows,
                    'one_original_test_pane':len(app.get_tab_by_id(tab.tab_id).sessions)==1,
                    'returned_to_idle_ssh':bool(prompt and prompt.state==api.PromptState.EDITING and not prompt.command),
                    'fixture_sessions_preserved':len(state['sessions'])==2 and {r['name'] for r in state['sessions']}=={'qa-base','qa-two'}}
            if not all(checks.values()): raise RuntimeError('QA final checks failed: '+json.dumps(checks))
            success=True
            result={'variant':variant,'native_iterm_sdk':True,'native_webkit_ui':True,'real_remote_ssh':True,
                    'ui_phases':ui.get('phase'),**checks}
            results.append(result)
            print(json.dumps(result),flush=True)
        finally:
            # Exact private socket and server PID only. Never clean a replacement
            # server or touch a production socket/session.
            cleanup='''set -eu
socket=SOCKET
[ ! -L "$socket" ] || exit 1
current=$(tmux -u -S "$socket" display-message -p '#{pid}' 2>/dev/null) || current=
if [ -n "$current" ] && [ "$current" != PID ]; then exit 1; fi
# A tmux CLI can return "lost server" even when kill-server took effect.
if [ "$current" = PID ]; then tmux -u -S "$socket" kill-server || :; fi
if tmux -u -S "$socket" display-message -p '#{pid}' >/dev/null 2>&1; then exit 1; fi
if [ -S "$socket" ] && [ ! -L "$socket" ]; then rm "$socket"; fi
rmdir "${socket%/*}"
'''.replace('SOCKET',shlex.quote(socket)).replace('PID',shlex.quote(pid))
            try: await remote(target,cleanup)
            except Exception: print(json.dumps({'qa_cleanup':'private_fixture_needs_inspection','variant':variant}),flush=True)
            if success and pane:
                await app.async_refresh()
                found=app.get_session_by_id(pane.session_id)
                if found and found.session_id not in original_ids: await found.async_close(force=True)
            elif pane: print(json.dumps({'qa_terminal_left_for_inspection':pane.session_id[:8],'variant':variant}),flush=True)
    await app.async_refresh()
    original=app.get_session_by_id(original_pane.session_id)
    if original: await original.async_activate(select_tab=True,order_window_front=False)
    print(json.dumps({'qa_complete':True,'results':results,'existing_terminals_preserved':all(app.get_session_by_id(sid) for sid in original_ids)}),flush=True)
