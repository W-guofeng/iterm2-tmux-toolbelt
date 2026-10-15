"""Single-flight state reads with a hard response deadline, even on bad cancel."""
import asyncio
import time
from .observability import event, waiting_stack


class StateReader:
    def __init__(self,service,timeout=4,work_timeout=15):
        self.service=service;self.timeout=timeout;self.work_timeout=work_timeout;self.task=None
        self.watch_task=None;self.stalled=asyncio.Event()
        self.last_error=None;self.last_status=None;self.completed_at=None
        self.timed_out=False;self.last_stack=[]

    def offline(self,message):
        return {'status':'offline','message':message+' 日志：~/Library/Logs/iterm2-tmux-toolbelt/toolbelt.log','context':None,'target':None,'sessions':[],'recovering':True}

    async def watch(self,task):
        done,_=await asyncio.wait({task},timeout=self.work_timeout)
        if not done and str(getattr(self.service,'phase','')).startswith('action.'):
            event('STATE_WAITING_ACTION',phase=self.service.phase)
            done,_=await asyncio.wait({task},timeout=20)
        if not done:
            self.last_stack=waiting_stack(task);self.last_error='STATE_STALLED'
            event('STATE_STALLED',phase=getattr(self.service,'phase','unknown'),stack=self.last_stack)
            task.cancel();self.stalled.set()

    async def read(self):
        if self.task is None:
            self.task=asyncio.create_task(self.service.snapshot())
            self.task.add_done_callback(lambda task: None if task.cancelled() else task.exception())
            self.watch_task=asyncio.create_task(self.watch(self.task))
            self.timed_out=False
        elif self.timed_out and not self.task.done():
            return self.offline('本地状态查询正在恢复，操作已禁用。请查看日志或稍后刷新；未执行任何操作。')
        task=self.task
        done,_=await asyncio.wait({task},timeout=self.timeout)
        if not done:
            self.last_stack=waiting_stack(task)
            self.last_error='STATE_TIMEOUT';self.timed_out=True
            event('STATE_TIMEOUT',phase=getattr(self.service,'phase','unknown'),stack=self.last_stack)
            # Keep one slow handshake alive; the independent work watchdog is bounded.
            self.last_status='offline'
            return self.offline('状态查询超时，操作已禁用。可能正在等待 iTerm2 API 或 SSH；未执行任何操作。详见本地日志。')
        if self.task is task: self.task=None
        try:
            state=task.result()
        except asyncio.CancelledError:
            return self.offline('状态查询已取消，正在恢复连接；未执行任何操作。')
        except Exception as exc:
            self.last_error='STATE_FAILED';self.last_status='offline'
            event('STATE_FAILED',error_type=type(exc).__name__,phase=getattr(self.service,'phase','unknown'))
            return self.offline('状态查询失败，操作已禁用。请查看本地日志。')
        self.completed_at=time.monotonic();self.last_error=None
        if self.last_status!=state.get('status'):
            event('STATE_APPLIED',status=state.get('status'))
        self.last_status=state.get('status')
        return state

    def health(self):
        return {'snapshot_pending':bool(self.task and not self.task.done()),'last_state_status':self.last_status,
                'last_state_age_seconds':round(time.monotonic()-self.completed_at,2) if self.completed_at else None,
                'last_error':self.last_error,'phase':getattr(self.service,'phase','idle'),
                'last_timeout_stack':self.last_stack}

    def close(self):
        if self.task and not self.task.done(): self.task.cancel()
        if self.watch_task and not self.watch_task.done(): self.watch_task.cancel()
