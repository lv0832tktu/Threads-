import io
import socket
import tempfile
import unittest
from pathlib import Path
from threads_publisher.media import MediaError, validate_image_file, validate_image_url, MAX_BYTES
try:
    from PIL import Image
except ImportError:
    Image = None


def resolver(host, port, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))]


class Response(io.BytesIO):
    def __init__(self, data, status=200, headers=None):
        super().__init__(data)
        self.status = status
        self.headers = headers or {}


class MediaTests(unittest.TestCase):
    def test_url_rejects_untrusted_destinations_before_fetch(self):
        for url in ('http://images.example/a', 'https://other.example/a',
                    'https://u:p@images.example/a', 'https://images.example:444/a',
                    'https://images.example/a#fragment'):
            with self.assertRaises(MediaError):
                validate_image_url(url, ['images.example'], resolver=resolver,
                                   transport=lambda *a, **k: self.fail('must not fetch'))

    def test_private_and_mixed_dns_rejected(self):
        for address in ('127.0.0.1', '10.1.2.3', '169.254.169.254', '::1', '192.0.2.1'):
            def unsafe(*args, **kwargs):
                return resolver(*args, **kwargs) + [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, 443))]
            with self.assertRaises(MediaError):
                validate_image_url('https://images.example/a', ['images.example'], resolver=unsafe,
                                   transport=lambda *a, **k: self.fail('must not fetch'))

    def test_redirect_and_large_header_rejected(self):
        for response in (Response(b'', 302), Response(b'', headers={'Content-Length': str(MAX_BYTES + 1)})):
            with self.assertRaises(MediaError):
                validate_image_url('https://images.example/a', ['images.example'], resolver=resolver,
                                   transport=lambda *a, **k: response)

    @unittest.skipIf(Image is None, 'optional Pillow not installed')
    def test_valid_signature_dimensions_and_checksum(self):
        output = io.BytesIO()
        Image.new('RGB', (320, 400)).save(output, format='PNG')
        data = output.getvalue()
        result = validate_image_url('https://images.example/a?version=1', ['images.example'], resolver=resolver,
                                    transport=lambda *a, **k: Response(data))
        self.assertEqual((result['mime'], result['width'], result['height']), ('image/png', 320, 400))
        self.assertEqual(result['bytes'], len(data))
        self.assertEqual(len(result['sha256']), 64)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'image.png'
            path.write_bytes(data)
            self.assertEqual(validate_image_file(path), result)

    @unittest.skipIf(Image is None, 'optional Pillow not installed')
    def test_invalid_dimensions_format_corruption(self):
        for dimensions, fmt in (((10, 10), 'PNG'), ((320, 320), 'GIF')):
            output = io.BytesIO()
            Image.new('RGB', dimensions).save(output, format=fmt)
            with self.assertRaises(MediaError):
                validate_image_url('https://images.example/a', ['images.example'], resolver=resolver,
                                   transport=lambda *a, **k: Response(output.getvalue()))
        with self.assertRaises(MediaError):
            validate_image_url('https://images.example/a', ['images.example'], resolver=resolver,
                               transport=lambda *a, **k: Response(b'not image'))
