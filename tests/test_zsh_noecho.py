import base64
import fcntl
import os
from pathlib import Path
import pty
import re
import select
import signal
import subprocess
import sys
import tempfile
import termios
import time
import unittest

TMUX=os.environ.get('TMUX_TEST_BINARY')
ZSH='/bin/zsh' if Path('/bin/zsh').exists() else '/usr/bin/zsh'
ROOT=Path(__file__).resolve().parents[1]
INTEGRATION=Path('/Applications/iTerm.app/Contents/Resources/iterm2_shell_integration.zsh')

@unittest.skipUnless(TMUX and Path(ZSH).exists() and INTEGRATION.exists(), 'Explicit tmux + local macOS zsh integration needed')
class ZleNoEchoTests(unittest.TestCase):
    def setUp(self):
        self.cleaned=False
        self.addCleanup(self.tearDown)
        self.temp=tempfile.TemporaryDirectory(prefix='tb-zle-',dir='/tmp')
        self.root=Path(self.temp.name);self.socket=str(self.root/'server.sock')
        self.cli('new-session','-d','-s','qa','/bin/sh','-c','trap : INT; while :; do sleep 1; done')
        self.pid=self.cli('display-message','-p','#{pid}')
        self.sid=self.cli('display-message','-p','#{session_id}')
        self.created=self.cli('display-message','-p','#{session_created}')
        self.history=self.root/'history'
        rc=('print -r -- PREEXISTING-SCROLLBACK\nHISTFILE='+str(self.history)+'\nHISTSIZE=1000\nSAVEHIST=1000\nsetopt INC_APPEND_HISTORY\n'
            'source '+str(INTEGRATION)+'\nsource '+str(ROOT/'toolbelt/zsh_attach.zsh')+'\nPROMPT="QA> "\n_capture_buffer_widget() { print -r -- "$BUFFER" > "$ZDOTDIR/buffer"; }\nzle -N _capture_buffer_widget\nbindkey "^X" _capture_buffer_widget\n')
        (self.root/'.zshrc').write_text(rc)
        self.master,self.slave=pty.openpty();os.set_blocking(self.master,False)
        self.initial=termios.tcgetattr(self.slave)
        argv=[ZSH,'-d','-i']
        code='import os,fcntl,termios;os.setsid();fcntl.ioctl(0,termios.TIOCSCTTY,0);os.tcsetpgrp(0,os.getpgrp());os.execv('+repr(ZSH)+','+repr(argv)+')'
        self.child=subprocess.Popen([sys.executable,'-c',code],stdin=self.slave,stdout=self.slave,stderr=self.slave,
            env={**os.environ,'ZDOTDIR':str(self.root),'TERM':'xterm-256color','TERM_PROGRAM':'iTerm.app','SHELL':ZSH,
                 'PATH':str(Path(TMUX).parent)+':'+os.environ['PATH'],'ITERM_SHELL_INTEGRATION_INSTALLED':''})
        self.data=bytearray();self.ready=None
        self.wait_ready()
        self.baseline=termios.tcgetattr(self.slave)

    def cli(self,*args):
        r=subprocess.run([TMUX,'-u','-S',self.socket,'-f','/dev/null',*args],capture_output=True,text=True,env={**os.environ,'TMUX':''},timeout=3)
        if r.returncode: raise RuntimeError('Isolated tmux command failed')
        return r.stdout.strip()

    def drain(self):
        while True:
            try: data=os.read(self.master,65536)
            except BlockingIOError: break
            except OSError: break
            if not data: break
            self.data.extend(data)
        matches=re.findall(rb'\x1b\]1337;SetUserVar=toolbelt_zsh_ready=([A-Za-z0-9+/=]+)\x07',self.data)
        if matches: self.ready=base64.b64decode(matches[-1]).decode().split('|')

    def wait(self,predicate,timeout=5):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            self.drain()
            if predicate(): return
            time.sleep(.02)
        clean=re.sub(rb'\x1b\][^\x07]*\x07',b'',bytes(self.data))
        self.fail('Timed out waiting for isolated ZLE state: '+repr(clean[-2000:]))

    def wait_ready(self,prior=None): self.wait(lambda:self.ready is not None and (prior is None or self.ready[4]!=prior))

    def protocol(self,op,nonce=None,shellpid=None,tty=None):
        fields=[op,self.socket,self.sid,nonce or self.ready[4],self.pid,self.created,
                tty or self.ready[3],shellpid or self.ready[2],self.ready[5]]
        result=subprocess.run(['/bin/sh','-c',(ROOT/'toolbelt/remote.sh').read_text(),'toolbelt','qa-frame'],
            input=('\t'.join(fields)+'\n').encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            env={**os.environ,'PATH':str(Path(TMUX).parent)+':'+os.environ['PATH']},timeout=4)
        self.assertEqual(result.returncode,0)
        frame=result.stdout.decode().strip().split('\t')
        self.assertEqual(frame[0],'qa-frame')
        return int(frame[1])

    def queue(self,pid=None):
        destination=Path(self.ready[5])/'request'
        if pid is not None:
            # Deliberately bypass producer checks to test the widget's own guard.
            record='\t'.join([self.socket,self.sid,pid,self.created,self.ready[4]])+'\n'
            destination.write_text(record);destination.chmod(0o600)
        else: self.assertEqual(self.protocol('prepare-attach'),0)
        return destination

    def key(self): os.write(self.master,b'\x1b[1337;777~')

    def attached(self):
        try: return bool(self.cli('list-clients','-F','#{client_pid}'))
        except RuntimeError: return False

    def assert_restored(self):
        current=termios.tcgetattr(self.slave)
        self.assertEqual(current[3] & (termios.ECHO|termios.ICANON|termios.ISIG),self.baseline[3] & (termios.ECHO|termios.ICANON|termios.ISIG))
        history=self.history.read_text() if self.history.exists() else ''
        self.assertNotIn('sh -c',history)
        self.assertNotIn('base64',history)
        self.assertNotIn('if-shell',history)
        self.assertNotIn('it2tb',history)

    def tearDown(self):
        if self.cleaned: return
        self.cleaned=True
        try: self.cli('kill-server')
        except (RuntimeError,subprocess.TimeoutExpired): pass
        if hasattr(self,'master'):
            os.close(self.master); del self.master
        if getattr(self,'child',None) and self.child.poll() is None:
            self.child.terminate()
            try: self.child.wait(timeout=2)
            except subprocess.TimeoutExpired: self.child.kill();self.child.wait(timeout=2)
        if getattr(self,'ready',None):
            directory=Path(self.ready[5])
            for name in ('request','identity'):
                try: (directory/name).unlink()
                except FileNotFoundError: pass
            try: directory.rmdir()
            except OSError: pass
        if hasattr(self,'master'): os.close(self.master)
        if hasattr(self,'slave'): os.close(self.slave)
        if hasattr(self,'temp'): self.temp.cleanup()

    def test_attach_detach_no_commands_or_history(self):
        nonce=self.ready[4];self.queue();self.key();self.wait(self.attached)
        second_id=self.cli('new-session','-d','-s','second','-P','-F','#{session_id}','sleep','600')
        # Later switches use the independent socket/control channel only.
        self.cli('switch-client','-c',self.ready[3],'-t',second_id)
        self.assertEqual(self.cli('list-clients','-F','#{session_id}'),second_id)
        self.cli('switch-client','-c',self.ready[3],'-t',self.sid)
        self.assertEqual(self.cli('list-clients','-F','#{session_id}'),self.sid)
        self.cli('detach-client','-t',self.ready[3]);self.wait_ready(nonce)
        self.assertNotIn(b'sh -c',self.data)
        self.assertIn(b'PREEXISTING-SCROLLBACK', self.data)
        self.assertNotIn(b'\x1b[3J', self.data)
        self.assert_restored()
        self.assertEqual(set(self.cli('list-sessions','-F','#{session_id}').splitlines()),{self.sid,second_id})

    def test_selected_sessions_really_render_in_terminal(self):
        first_marker=b'IT2TB_ZLE_FIRST_SCREEN'
        second_marker=b'IT2TB_ZLE_SECOND_SCREEN'
        self.cli('respawn-pane','-k','-t',self.sid,'printf "IT2TB_ZLE_FIRST_SCREEN\\n"; sleep 600')
        self.queue(); self.key()
        self.wait(lambda: first_marker in self.data)
        second=self.cli('new-session','-d','-s','second','-P','-F','#{session_id}',
                        'printf "IT2TB_ZLE_SECOND_SCREEN\\n"; sleep 600')
        self.cli('switch-client','-c',self.ready[3],'-t',second)
        self.wait(lambda: second_marker in self.data)
        self.data.clear()
        self.cli('switch-client','-c',self.ready[3],'-t',self.sid)
        self.wait(lambda: first_marker in self.data)
        nonce=self.ready[4]
        self.cli('detach-client','-t',self.ready[3]);self.wait_ready(nonce)
        self.assert_restored()

    def test_failure_visible_and_tty_restored(self):
        self.queue(pid='99999999');self.key();self.wait(lambda:b'attachment cancelled' in self.data)
        self.assertIn(b'attachment cancelled',self.data)
        self.assertFalse(self.attached());self.assert_restored()

    def test_partial_input_never_executed_or_lost(self):
        os.write(self.master,b'unfinished');time.sleep(.1)
        request=self.queue();self.key();self.wait(lambda:not request.exists())
        self.assertFalse(self.attached())
        os.write(self.master,b'\x18');self.wait(lambda:(self.root/'buffer').exists())
        self.assertEqual((self.root/'buffer').read_text(), 'unfinished\n')
        os.write(self.master,b'\x03');self.wait_ready(self.ready[4]);self.assert_restored()

    def test_ctrl_c_pending_request_not_replayed(self):
        self.queue();nonce=self.ready[4]
        os.write(self.master,b'\x03');self.wait_ready(nonce);self.key();time.sleep(.2)
        self.assertFalse(self.attached());self.assert_restored()

    def test_stale_nonce_and_expired_request_are_consumed(self):
        request=self.queue()
        request.write_text(request.read_text().replace(self.ready[4], '0'*32))
        self.key();self.wait(lambda:not request.exists())
        self.assertFalse(self.attached())
        request=self.queue();os.utime(request,(time.time()-60,time.time()-60))
        self.key();self.wait(lambda:not request.exists())
        self.assertFalse(self.attached());self.assert_restored()

    def test_producer_refuses_wrong_process_and_never_replaces_request(self):
        self.assertEqual(self.protocol('prepare-attach',shellpid='99999999'),30)
        request=self.queue();first=request.read_bytes()
        self.assertEqual(self.protocol('prepare-attach'),30)
        self.assertEqual(request.read_bytes(),first)
        self.assertEqual(self.protocol('cancel-attach',nonce='f'*32),0)
        self.assertEqual(request.read_bytes(),first)
        self.assertEqual(self.protocol('cancel-attach'),0)
        self.assertFalse(request.exists())

    def test_request_symlink_is_not_followed(self):
        request=self.queue();contents=request.read_text();request.unlink()
        outside=self.root/'outside';outside.write_text(contents)
        request.symlink_to(outside)
        self.key();time.sleep(.15);self.drain()
        self.assertFalse(self.attached())
        self.assertEqual(outside.read_text(),contents)
        self.assertTrue(request.is_symlink())
        self.assert_restored()

    def test_ctrl_c_inside_client_then_detach_restores(self):
        nonce=self.ready[4];self.queue();self.key();self.wait(self.attached)
        os.write(self.master,b'\x03');time.sleep(.2)
        self.assertTrue(self.attached())
        self.cli('detach-client','-t',self.ready[3]);self.wait_ready(nonce);self.assert_restored()

    def test_hangup_ends_only_own_test_shell_and_no_global_tty_change(self):
        self.queue();self.key();self.wait(self.attached)
        os.close(self.master); del self.master
        os.kill(self.child.pid,signal.SIGHUP)
        try: self.child.wait(timeout=3)
        except subprocess.TimeoutExpired: self.fail('ZLE failed to exit on its own test HUP')
        self.assertEqual(self.cli('list-sessions','-F','#{session_id}'),self.sid)
