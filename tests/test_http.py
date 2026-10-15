import asyncio
import json
import unittest
from toolbelt.http import LocalServer


class Stub:
    def __init__(self): self.actions = []
    async def snapshot(self): return {"status":"online", "sessions":[]}
    async def action(self, value): self.actions.append(value); return {"ok":True}


class HTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.service = Stub()
        self.server = LocalServer(self.service)
        self.url = await self.server.start()
        self.port = self.server.server.sockets[0].getsockname()[1]
        self.host = f"127.0.0.1:{self.port}"

    async def asyncTearDown(self): await self.server.close()

    async def request(self, method="GET", path="/api/state", headers=None, body=b"", raw=None):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        values = {"Host":self.host, "Authorization":"Bearer " + self.server.token}
        values.update(headers or {})
        request = f"{method} {path} HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k,v in values.items()) + f"Content-Length: {len(body)}\r\n\r\n"
        writer.write(raw if raw else request.encode() + body)
        await writer.drain()
        output = await reader.read()
        writer.close(); await writer.wait_closed()
        head, data = output.split(b"\r\n\r\n", 1)
        return int(head.split()[1]), head.decode(), data

    async def test_loopback_token_not_in_http_url_or_page(self):
        self.assertTrue(self.url.startswith(f"http://{self.host}/#"))
        self.assertEqual(self.server.server.sockets[0].getsockname()[0], "127.0.0.1")
        status, head, data = await self.request(path="/")
        self.assertEqual(status, 200)
        self.assertNotIn(self.server.token.encode(), data)
        self.assertIn("default-src 'none'", head)
        self.assertIn("Cache-Control: no-store", head)
        self.assertIn("Referrer-Policy: same-origin", head)
        self.assertNotIn("Access-Control-Allow-Origin", head)

    async def test_token_host_origin_fetchsite(self):
        for headers in ({"Authorization":""}, {"Host":"attacker.invalid"}, {"Origin":"https://evil.invalid"}, {"Sec-Fetch-Site":"cross-site"}):
            self.assertEqual((await self.request(headers=headers))[0], 403)
        self.assertEqual((await self.request())[0], 200)

    async def test_post_requires_sameorigin_json(self):
        body = json.dumps({"action":"create"}).encode()
        for headers in ({}, {"Content-Type":"application/json"}, {"Origin":self.server.origin}, {"Origin":"null", "Content-Type":"application/json"}):
            self.assertEqual((await self.request("POST", "/api/action", headers, body))[0], 403)
        self.assertEqual(self.service.actions, [])
        status, _, _ = await self.request("POST", "/api/action", {"Origin":self.server.origin, "Content-Type":"application/json"}, body)
        self.assertEqual(status, 200)
        self.assertEqual(self.service.actions, [{"action":"create"}])

    async def test_invalid_json_large_body_and_paths(self):
        headers = {"Origin":self.server.origin, "Content-Type":"application/json"}
        self.assertEqual((await self.request("POST", "/api/action", headers, b"invalid"))[0], 400)
        self.assertEqual((await self.request("POST", "/api/action", headers, b"x" * 8193))[0], 413)
        for path in ("/../core.py", "/api/action?x=1", "/api/state?token=x"):
            self.assertEqual((await self.request(path=path))[0], 404)
        self.assertEqual(self.service.actions, [])

    async def test_duplicate_headers_and_chunked_rejected(self):
        for extra in (f"Host: {self.host}\r\n", "Transfer-Encoding: chunked\r\n"):
            raw = (f"GET /api/state HTTP/1.1\r\nHost: {self.host}\r\n{extra}\r\n").encode()
            self.assertEqual((await self.request(raw=raw))[0], 400)

    async def test_webview_render_report_authenticated_same_origin(self):
        payload = json.dumps({"status":"online", "revision":self.server.ui_revision}).encode()
        headers={"Origin":self.server.origin,"Content-Type":"application/json","User-Agent":"Mozilla iTerm2"}
        self.assertEqual((await self.request("POST","/api/ui-status",headers,payload))[0],200)
        status, _, body = await self.request(path="/api/diagnostics")
        self.assertEqual(status,200)
        data=json.loads(body)
        self.assertEqual(data["last_ui_client"],"iterm2-webview")
        self.assertEqual(data["last_ui_status"],"online")
        self.assertEqual(self.service.actions,[])
        headers["Origin"]="null"
        self.assertEqual((await self.request("POST","/api/ui-status",headers,payload))[0],403)
