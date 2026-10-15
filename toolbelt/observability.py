"""Private, rotating event logs. Never record targets, commands or tokens."""
import datetime
import json
import logging
import logging.handlers
import os
from pathlib import Path

LOG_PATH = Path.home() / 'Library/Logs/iterm2-tmux-toolbelt/toolbelt.log'
_logger = None


class PrivateHandler(logging.handlers.RotatingFileHandler):
    def _open(self):
        fd=os.open(self.baseFilename,os.O_CREAT|os.O_APPEND|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        os.fchmod(fd,0o600)
        return os.fdopen(fd,'a',encoding='utf-8')


def configure(path=LOG_PATH):
    global _logger
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.parent.is_symlink() or path.parent.stat().st_uid!=os.getuid():
        raise RuntimeError('Invalid private log directory')
    path.parent.chmod(0o700)
    logger=logging.getLogger('iterm2-tmux-toolbelt.events')
    logger.setLevel(logging.INFO);logger.propagate=False
    if not logger.handlers:
        handler=PrivateHandler(path,maxBytes=512000,backupCount=2,encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(message)s'))
        logger.addHandler(handler)
    _logger=logger
    return str(path)


def event(code,**fields):
    if _logger is None: return
    data={'time':datetime.datetime.now(datetime.timezone.utc).isoformat(),'event':code,**fields}
    _logger.info(json.dumps(data,ensure_ascii=False))


def waiting_stack(task):
    """Function/line metadata only; never inspect coroutine locals or values."""
    result=[];item=task.get_coro()
    for _ in range(20):
        frame=getattr(item,'cr_frame',None) or getattr(item,'gi_frame',None)
        if frame:
            result.append({'file':Path(frame.f_code.co_filename).name,'function':frame.f_code.co_name,'line':frame.f_lineno})
        item=getattr(item,'cr_await',None) or getattr(item,'gi_yieldfrom',None)
        if item is None: break
    return result
