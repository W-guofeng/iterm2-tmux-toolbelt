"""Explicit read-only SDK setup diagnostics; no terminal text or host values."""
import asyncio
import json
import socket
from .process import ForegroundSSH
from .ssh import QueryChannel, SSHTransport
from .core import ToolbeltError


async def inspect_setup(api,connection):
    app=await api.async_get_app(connection)
    detector=ForegroundSSH()
    local=socket.gethostname().rstrip('.').lower()
    for window in app.terminal_windows:
        for tab in window.tabs:
            for pane in tab.sessions:
                try:
                    spec=await detector.identify(pane)
                except ToolbeltError:
                    continue
                data={'pane':pane.session_id[:8],'tab':tab.tab_id,'bootstrap':spec.bootstrap,
                      'active':app.current_terminal_window==window and window.current_tab==tab and tab.current_session==pane}
                values={}
                for name in ('hostname','username','sshIntegrationLevel','shell'):
                    values[name]=await pane.async_get_variable(name)
                host=values['hostname']; user=values['username']
                data.update({'host_type':type(host).__name__,'user_type':type(user).__name__,
                             'host_present':bool(host),'user_present':bool(user),
                             'host_is_local':isinstance(host,str) and host.rstrip('.').lower() in (local,local.split('.')[0]),
                             'ssh_integration_level':values['sshIntegrationLevel'],
                             'shell_kind':values['shell'].rsplit('/',1)[-1] if isinstance(values['shell'],str) else None})
                prompt=await api.async_get_last_prompt(connection,pane.session_id)
                data['prompt_state']=prompt.state.name if prompt else None
                data['input_empty']=not bool(prompt.command) if prompt else None
                channel=QueryChannel(spec)
                try:
                    transport=SSHTransport(channel)
                    await asyncio.wait_for(transport.initialize(),10)
                    data['query_identity']='verified'
                    data['host_exact_match']=isinstance(host,str) and host.rstrip('.').lower()==transport.host.rstrip('.').lower()
                    data['host_short_match']=isinstance(host,str) and host.rstrip('.').lower().split('.')[0]==transport.host.rstrip('.').lower().split('.')[0]
                    data['user_exact_match']=user==transport.user
                    data['user_empty']=user in ('',None)
                    setup=await channel.request('setup')
                    lines=setup.splitlines()
                    data['remote_login_shell']=lines[0].rsplit('/',1)[-1] if lines else None
                    data['remote_integration_files']=[line for line in lines[1:] if line.startswith(('.bashrc|','.bash_profile|','.profile|','.zshrc|','.iterm2_'))]
                except ToolbeltError as exc: data['query_identity']=exc.code
                except asyncio.TimeoutError: data['query_identity']='timeout'
                finally: await channel.close()
                print(json.dumps(data,ensure_ascii=False),flush=True)
