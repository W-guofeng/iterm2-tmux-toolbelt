"""Recognize only the exact iTerm2 3.7.4 it2ssh conductor bootstrap.

Never executes/evaluates the payload or decodes its inherited Env report.
The fixed tail hash was checked against installed official Resources/it2ssh.
"""
import base64
import hashlib
import re
import shlex

TAIL_MARKER = ';s="";IFS="";'
TAIL_SHA256 = '069df3671cfcda23bdafffcdd011b5270b06571470642057b490e599c6a4f522'
INTRO = re.compile(
    r'\x1b\]1337;Env=report=all:[A-Za-z0-9+/=]{1,100000}\x07'
    r'\x1b\]1337;it2ssh=[A-Za-z0-9_-]{1,128} [0-9]{1,20} '
    r'[A-Za-z0-9+/=]{1,256} (?P<args>[A-Za-z0-9+/=]{1,10000})\x07'
    r'\x1b\]1337;SendConductor=v=3\x07')


def original_iterm_ssh_args(remote):
    """Return original SSH argv only for the exact known bootstrap; else None."""
    try:
        if sum(len(arg) for arg in remote) > 130000: return None
        command = shlex.split(' '.join(remote))
        if len(command) != 5 or command[:4] != ['exec','sh','-c','eval "$(echo "$0")"']:
            return None
        payload = command[4]
        prefix = 'stty -echo -isig; printf "%s '
        before, marker, rest = payload.partition(TAIL_MARKER)
        if not marker or not before.startswith(prefix) or not before.endswith('"'): return None
        if hashlib.sha256((marker+rest).encode()).hexdigest() != TAIL_SHA256: return None
        match = INTRO.fullmatch(before[len(prefix):-1])
        if not match: return None
        # Only the original SSH argument list is decoded, never the Env report
        # or the conductor token. It must itself parse as a plain connection.
        args = base64.b64decode(match['args'], validate=True).decode('utf-8','strict')
        return tuple(shlex.split(args))
    except (ValueError, UnicodeError):
        return None
