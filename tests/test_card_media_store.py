from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import urlopen, Request

from PIL import Image
from app.card_media_store import CardMediaPublisher, make_published_handler


class CardMediaStoreTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.image=self.root/'source.png'
        Image.new('RGB',(8,8),'#fff7ec').save(self.image)
        self.now=datetime(2030,6,19,12,tzinfo=timezone.utc)
        self.cache=self.root/'private'
        self.publisher=CardMediaPublisher(self.cache,'https://example.com')

    def test_random_capabilities_have_no_event_data(self):
        first=self.publisher.publish(self.image,now=self.now)
        second=self.publisher.publish(self.image,now=self.now)
        self.assertNotEqual(first,second)
        self.assertRegex(urlsplit(first).path,r'^/cards/private-[a-f0-9]{64}\.png$')
        metadata=list(self.cache.glob('*.json'))
        self.assertEqual(len(metadata),2)
        self.assertEqual(set(json.loads(metadata[0].read_text())),{'created_at','expires_at','sha256'})

    def test_origin_rejects_private_hosts_and_credentials(self):
        for origin in ('http://example.com','https://localhost','https://127.0.0.1',
                       'https://user:pass@example.com','https://example.com/path','https://example.com?q=1'):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                CardMediaPublisher(self.cache,origin)

    def test_bad_file_is_not_published(self):
        self.image.write_text('not png')
        with self.assertRaises(ValueError):
            self.publisher.publish(self.image,now=self.now)
        self.assertFalse(self.cache.exists())

    def test_failed_public_verification_removes_unpublished_capability(self):
        publisher=CardMediaPublisher(self.cache,'https://example.com',verify_public=True)
        session=Mock()
        session.__enter__=Mock(return_value=session)
        session.__exit__=Mock(return_value=False)
        session.get.side_effect=TimeoutError()
        with patch('app.card_media_store.requests.Session',return_value=session), self.assertRaises(ValueError):
            publisher.publish(self.image,now=self.now)
        self.assertEqual(list(self.cache.iterdir()),[])

    def test_public_route_expires_and_integrity_mismatch_is_not_served(self):
        link=self.publisher.publish(self.image,now=self.now)
        handler=make_published_handler(self.cache,clock=lambda:self.now)
        server=ThreadingHTTPServer(('127.0.0.1',0),handler)
        worker=threading.Thread(target=server.serve_forever,daemon=True)
        worker.start()
        origin='http://127.0.0.1:'+str(server.server_address[1])
        route=urlsplit(link).path
        try:
            with urlopen(origin+route,timeout=2) as reply:
                self.assertEqual(reply.read(),self.image.read_bytes())
                self.assertEqual(reply.headers['Cache-Control'],'no-store')
            with urlopen(Request(origin+route,method='HEAD'),timeout=2) as reply:
                self.assertEqual(reply.read(),b'')
            for bad in ('/','/.env','/cards/../source.png',route+'?x=1'):
                with self.assertRaises(HTTPError) as failure:
                    urlopen(origin+bad,timeout=2)
                self.assertEqual(failure.exception.code,404)
            list(self.cache.glob('*.png'))[0].write_bytes(b'broken')
            with self.assertRaises(HTTPError):
                urlopen(origin+route,timeout=2)
            list(self.cache.glob('*.png'))[0].write_bytes(self.image.read_bytes())
            self.now+=timedelta(seconds=600)
            with self.assertRaises(HTTPError):
                urlopen(origin+route,timeout=2)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)


if __name__=='__main__':
    unittest.main()
