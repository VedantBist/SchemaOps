"""Isolated regression checks: no database, collector, or live data is used."""
import asyncio
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

import httpx

from ml.ulpf import api, bundle
from ml.ulpf.store import Store


class ResponsivenessTests(unittest.TestCase):
    def test_export_does_not_block_other_requests(self):
        started, release = threading.Event(), threading.Event()

        def slow_export(*args, **kwargs):
            started.set()
            release.wait(3)
            return dict(file='test.tar.gz', bytes=1, events=1, sha256='test', signer='test')

        async def exercise():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url='http://test') as client:
                export = asyncio.create_task(client.post('/bundles', json={'minutes': 1}))
                try:
                    self.assertTrue(await asyncio.to_thread(started.wait, 2))
                    health = await asyncio.wait_for(client.get('/health'), .75)
                    self.assertEqual(health.status_code, 200)
                    self.assertFalse(export.done(), 'Export blocked unrelated API requests')
                    onboarding = await asyncio.wait_for(client.get('/onboarding'), .75)
                    self.assertEqual(onboarding.status_code, 200)
                finally:
                    release.set()
                    response = await export
                self.assertEqual(response.status_code, 200)

        fake_store = Mock()
        fake_store.rows.return_value = [{'id': 'test'}]
        with patch.object(api, 'store', fake_store), patch.object(bundle, 'create', slow_export):
            asyncio.run(exercise())

    def test_api_threads_have_independent_connections(self):
        store = Store('unused', thread_local=True)
        barrier = threading.Barrier(2)
        def connect():
            first = store.conn()
            barrier.wait(timeout=2)
            self.assertIs(first, store.conn())
            return first
        with patch('psycopg.connect', side_effect=lambda *a, **k: Mock(closed=False)):
            with ThreadPoolExecutor(max_workers=2) as pool:
                a, b = list(pool.map(lambda _: connect(), range(2)))
        self.assertIsNot(a, b)


if __name__ == '__main__':
    unittest.main()
