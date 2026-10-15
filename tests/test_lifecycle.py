import asyncio
import json
import time
import unittest
from types import SimpleNamespace as Obj
from toolbelt.http import LocalServer
from toolbelt.runtime import wait_for_disconnect, wait_for_disconnect_or_stall, bounded_operation
from toolbelt.state import StateReader


class StateDeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancellation_resistant_snapshot_cannot_block_state_or_health(self):
        release=asyncio.Event()
        class Blocked:
            phase='shell.describe'
            calls=0
            async def snapshot(self):
                self.calls+=1
                try: await release.wait()
                except asyncio.CancelledError: await release.wait()
                return {'status':'online','sessions':[]}
        service=Blocked();server=LocalServer(service);server.states.timeout=.04
        await server.start()
        port=server.server.sockets[0].getsockname()[1]
        async def get(path):
            reader,writer=await asyncio.open_connection('127.0.0.1',port)
            writer.write((f'GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nAuthorization: Bearer '+server.token+'\r\n\r\n').encode())
            await writer.drain();result=await asyncio.wait_for(reader.read(),1)
            writer.close();await writer.wait_closed()
            return json.loads(result.split(b'\r\n\r\n',1)[1])
        try:
            start=time.monotonic();state=await get('/api/state')
            self.assertLess(time.monotonic()-start,.5)
            self.assertEqual(state['status'],'offline')
            self.assertIsNone(state['context'])
            health=await get('/api/health')
            self.assertEqual(health['service'],'running')
            self.assertEqual(health['last_error'],'STATE_TIMEOUT')
            self.assertEqual(health['phase'],'shell.describe')
            start=time.monotonic();await get('/api/state')
            self.assertLess(time.monotonic()-start,.2)
            self.assertEqual(service.calls,1)
        finally:
            release.set();await asyncio.sleep(.01);await server.close()

    async def test_slow_valid_snapshot_is_returned_after_soft_deadline(self):
        calls=[]
        class Slow:
            async def snapshot(self):
                calls.append(1);await asyncio.sleep(.05)
                return {'status':'online','sessions':[]}
        reader=StateReader(Slow(),timeout=.01,work_timeout=1)
        self.assertEqual((await reader.read())['status'],'offline')
        await asyncio.sleep(.06)
        self.assertEqual((await reader.read())['status'],'online')
        self.assertEqual(len(calls),1)
        reader.close()

    async def test_stalled_query_signals_recovery_without_waiting_for_cancel(self):
        release=asyncio.Event()
        class Stuck:
            async def snapshot(self):
                try: await release.wait()
                except asyncio.CancelledError: await release.wait()
                return {'status':'online','sessions':[]}
        reader=StateReader(Stuck(),timeout=.01,work_timeout=.04)
        try:
            await reader.read()
            await asyncio.wait_for(reader.stalled.wait(),.2)
            self.assertEqual(reader.last_error,'STATE_STALLED')
            self.assertEqual((await reader.read())['status'],'offline')
        finally:
            release.set();await asyncio.sleep(.01);reader.close()

    async def test_concurrent_reads_share_one_snapshot_and_release_next_read(self):
        calls=[]
        class Service:
            async def snapshot(self):
                calls.append(1);await asyncio.sleep(.02)
                return {'status':'online','sessions':[]}
        reader=StateReader(Service(),timeout=1)
        results=await asyncio.gather(reader.read(),reader.read(),reader.read())
        self.assertEqual(len(calls),1)
        self.assertTrue(all(r['status']=='online' for r in results))
        await reader.read();self.assertEqual(len(calls),2)


class ConnectionLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_disconnect_wakes_lifecycle_instead_of_forever_event(self):
        closed=asyncio.Event()
        async def wait_closed(): await closed.wait()
        connection=Obj(websocket=Obj(wait_closed=wait_closed,close_code=1006))
        task=asyncio.create_task(wait_for_disconnect_or_stall(connection,asyncio.Event()))
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        closed.set()
        self.assertEqual(await asyncio.wait_for(task,.1),1006)

    async def test_stalled_reader_wakes_recovery_without_closing_user_sessions(self):
        never=asyncio.Event();stalled=asyncio.Event()
        async def wait_closed(): await never.wait()
        connection=Obj(websocket=Obj(wait_closed=wait_closed,close_code=None))
        stalled.set()
        result=await asyncio.wait_for(wait_for_disconnect_or_stall(connection,stalled),.1)
        self.assertEqual(result,'state-stalled')

    async def test_startup_operation_has_bounded_deadline(self):
        with self.assertRaises(asyncio.TimeoutError):
            await bounded_operation(asyncio.sleep(60),.02,'startup.app')
