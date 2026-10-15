"""Small bounded loopback HTTP service; no cookies, CORS or persistent tokens."""
import asyncio
import json
import re
import secrets
import time
from .state import StateReader
from .observability import LOG_PATH, event
from pathlib import Path
from .core import ToolbeltError


class LocalServer:
    def __init__(self, service, web_root=None):
        self.service = service
        self.states = StateReader(service)
        self.started_at = time.monotonic()
        self.runtime = {"api_connected":None}
        self.token = secrets.token_urlsafe(32)
        self.web_root = web_root or Path(__file__).parent / "web"
        self.server = None
        self.active = 0
        self.origin = None
        self.diagnostics = {"state_requests":0, "ui_reports":0, "last_ui_client":None, "last_ui_status":None, "last_rejection":None}
        self.ui_revision = "v0-0-1"

    async def start(self):
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0, limit=16384)
        port = self.server.sockets[0].getsockname()[1]
        self.origin = "http://127.0.0.1:" + str(port)
        return self.origin + "/#" + self.token

    async def close(self):
        self.states.close()
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    async def reply(self, writer, status, data, mime="application/json; charset=utf-8"):
        if not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=True).encode()
        reasons = {200: "OK", 400: "Bad Request", 403: "Forbidden", 404: "Not Found",
                   405: "Method Not Allowed", 409: "Conflict", 413: "Content Too Large",
                   431: "Request Header Fields Too Large", 503: "Service Unavailable", 504: "Gateway Timeout"}
        headers = [f"HTTP/1.1 {status} {reasons.get(status, 'Error')}",
                   "Content-Type: " + mime, "Content-Length: " + str(len(data)),
                   "Connection: close", "Cache-Control: no-store", "Referrer-Policy: same-origin",
                   "X-Content-Type-Options: nosniff", "X-Frame-Options: DENY",
                   "Content-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"]
        writer.write(("\r\n".join(headers) + "\r\n\r\n").encode() + data)
        await writer.drain()

    async def handle(self, reader, writer):
        self.active += 1
        try:
            if self.active > 16:
                await self.reply(writer, 503, {"error": "请求过多。"})
                return
            try:
                raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 3)
                lines = raw.decode("ascii").split("\r\n")
                method, path, version = lines[0].split(" ")
                if version != "HTTP/1.1":
                    raise ValueError()
                headers = {}
                for line in lines[1:-2]:
                    key, value = line.split(":", 1)
                    if key.lower() in headers or key != key.strip():
                        raise ValueError()
                    headers[key.lower()] = value.strip()
                if "transfer-encoding" in headers:
                    raise ValueError()
                length = int(headers.get("content-length", "0"))
                if length < 0:
                    raise ValueError()
            except (ValueError, UnicodeError, asyncio.IncompleteReadError):
                await self.reply(writer, 400, {"error": "无效 HTTP 请求。"})
                return
            except asyncio.LimitOverrunError:
                await self.reply(writer, 431, {"error": "请求头过大。"})
                return
            if headers.get("host") != self.origin.removeprefix("http://"):
                await self.reply(writer, 403, {"error": "无效主机。"})
                return
            if headers.get("origin") not in (None, self.origin) or headers.get("sec-fetch-site") == "cross-site":
                self.diagnostics["last_rejection"] = "opaque-origin" if headers.get("origin") == "null" else "cross-site"
                await self.reply(writer, 403, {"error": "拒绝跨站请求。"})
                return
            assets = {"/": ("index.html", "text/html; charset=utf-8"),
                      "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                      "/i18n.js": ("i18n.js", "text/javascript; charset=utf-8"),
                      "/style.css": ("style.css", "text/css; charset=utf-8")}
            if path in assets and method == "GET" and length == 0:
                filename, mime = assets[path]
                await self.reply(writer, 200, (self.web_root / filename).read_bytes(), mime)
                return
            authorization = headers.get("authorization", "")
            if not secrets.compare_digest(authorization, "Bearer " + self.token):
                await self.reply(writer, 403, {"error": "无效访问令牌。"})
                return
            if length > 8192:
                await self.reply(writer, 413, {"error": "请求体过大。"})
                return
            if path == "/api/state" and method == "GET" and length == 0:
                self.diagnostics["state_requests"] += 1
                await self.reply(writer, 200, {**await self.states.read(), "ui_revision":self.ui_revision})
            elif path == "/api/health" and method == "GET" and length == 0:
                await self.reply(writer,200,{"service":"running","uptime_seconds":round(time.monotonic()-self.started_at,2),"log_path":str(LOG_PATH),**self.runtime,**self.states.health()})
            elif path == "/api/diagnostics" and method == "GET" and length == 0:
                await self.reply(writer, 200, {**self.diagnostics, "ui_revision":self.ui_revision,"log_path":str(LOG_PATH),**self.runtime,**self.states.health()})
            elif path in ("/api/action", "/api/ui-status") and method == "POST":
                if headers.get("origin") != self.origin or headers.get("content-type") != "application/json":
                    await self.reply(writer, 403, {"error": "仅接受同源 JSON 请求。"})
                    return
                try:
                    body = await asyncio.wait_for(reader.readexactly(length), 3)
                    payload = json.loads(body)
                except (ValueError, UnicodeError, asyncio.IncompleteReadError):
                    await self.reply(writer, 400, {"error": "无效 JSON。"})
                    return
                if path == "/api/ui-status":
                    if not isinstance(payload, dict) or set(payload) != {"status", "revision"} or payload.get("status") not in ("online", "unsupported", "offline") or payload.get("revision") != self.ui_revision:
                        await self.reply(writer, 400, {"error":"无效 UI 状态。"})
                        return
                    self.diagnostics["ui_reports"] += 1
                    self.diagnostics["last_ui_status"] = payload["status"]
                    user_agent = headers.get("user-agent", "")
                    client = "iterm2-webview" if "iTerm2" in user_agent else "native-webkit-test" if "ToolbeltQA" in user_agent else "other"
                    if client != self.diagnostics["last_ui_client"]:
                        print("面板状态请求已完成并渲染（" + client + "）。", flush=True)
                    event("UI_RENDERED",client=client,status=payload["status"],revision=self.ui_revision)
                    self.diagnostics["last_ui_client"] = client
                    await self.reply(writer, 200, {"ok":True})
                else:
                    action=payload.get('action') if isinstance(payload,dict) else None
                    known=action if action in {'switch','create','rename','delete','detach','socket','reconnect','setup','setup-noecho'} else 'invalid'
                    client='iterm2-webview' if 'iTerm2' in headers.get('user-agent','') else 'other'
                    request_id=secrets.token_hex(6)
                    sid=payload.get('id') if isinstance(payload,dict) else None
                    sid=sid if isinstance(sid,str) and re.fullmatch(r'\$[0-9]{1,20}',sid) else None
                    event('ACTION_BEGIN',action=known,client=client,request_id=request_id,session_id=sid)
                    try:
                        result=await self.service.action(payload)
                    except Exception as exc:
                        event('ACTION_FAILED',action=known,client=client,request_id=request_id,error_type=type(exc).__name__,code=getattr(exc,'code','unknown'))
                        raise
                    event('ACTION_DONE',action=known,client=client,request_id=request_id)
                    await self.reply(writer,200,result)
            else:
                await self.reply(writer, 404, {"error": "未知路径或方法。"})
        except ToolbeltError as exc:
            await self.reply(writer, exc.status, {"error": str(exc), "code": exc.code})
        except (asyncio.TimeoutError, ConnectionError, OSError):
            pass
        except Exception:
            # Never expose private tmux/iTerm2 exception data.
            try:
                await self.reply(writer, 503, {"error": "服务暂不可用。"})
            except ConnectionError:
                pass
        finally:
            self.active -= 1
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
