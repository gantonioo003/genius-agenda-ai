import importlib.util
import threading
import tempfile
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen, Request
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('card_media_server', ROOT / 'app/card_media_server.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CardMediaServerTest(unittest.TestCase):
    def test_only_exact_png_route_is_public(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(temporary.cleanup)
        image = Path(temporary.name) / 'only-card.png'
        Image.new('RGB', (8, 8), '#fff7ec').save(image)
        handler, route = module.make_handler(image)
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        origin = 'http://127.0.0.1:' + str(server.server_address[1])
        try:
            with urlopen(origin + route, timeout=2) as reply:
                self.assertEqual(reply.headers['Content-Type'], 'image/png')
                self.assertEqual(reply.read(), image.read_bytes())
            with urlopen(Request(origin + route, method='HEAD'), timeout=2) as reply:
                self.assertEqual(reply.read(), b'')
            for bad in ('/', '/.env', '/cards/../.env', route+'?file=.env'):
                with self.assertRaises(HTTPError) as caught:
                    urlopen(origin+bad, timeout=2)
                self.assertEqual(caught.exception.code, 404)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
