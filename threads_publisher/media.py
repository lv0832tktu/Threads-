"""Conservative image preflight; no uploads or publication.

Public media fetches are host-allowlisted, HTTPS-only and pinned to a checked
public address. API acceptance can only be confirmed by Meta at publication.
"""
import hashlib
import http.client
import io
import ipaddress
import socket
import ssl
from pathlib import Path
from urllib.parse import urlsplit

MAX_BYTES = 8 * 1024 * 1024


class MediaError(ValueError):
    pass


def _validate_bytes(data):
    if not data or len(data) > MAX_BYTES:
        raise MediaError('Image is empty or exceeds 8 MiB')
    try:
        from PIL import Image
    except ImportError:
        raise MediaError('Install requirements-media.txt for image validation') from None
    try:
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
            if image.format not in ('JPEG', 'PNG'):
                raise MediaError('Only JPEG and PNG images are accepted by local preflight')
            if not (320 <= width <= 10000 and 320 <= height <= 10000):
                raise MediaError('Image dimensions must be between 320 and 10000 pixels')
            if width * height > 40_000_000 or not 0.1 <= width / height <= 10:
                raise MediaError('Image dimensions exceed conservative safety limits')
            if getattr(image, 'n_frames', 1) != 1:
                raise MediaError('Animated images are unsupported')
            mime = 'image/jpeg' if image.format == 'JPEG' else 'image/png'
            image.verify()
        # verify checks container integrity; load also verifies decoded pixels.
        with Image.open(io.BytesIO(data)) as image:
            image.load()
    except MediaError:
        raise
    except Exception:
        raise MediaError('Invalid or unsupported image data') from None
    return {'mime': mime, 'bytes': len(data), 'width': width, 'height': height,
            'sha256': hashlib.sha256(data).hexdigest()}


def validate_image_file(path):
    try:
        with Path(path).open('rb') as source:
            data = source.read(MAX_BYTES + 1)
    except OSError:
        raise MediaError('Cannot read local image') from None
    return _validate_bytes(data)


def _public_addresses(host, resolver):
    try:
        addresses = {entry[4][0] for entry in resolver(host, 443, type=socket.SOCK_STREAM)}
        if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise ValueError()
    except Exception:
        raise MediaError('Media host must resolve exclusively to public IP addresses') from None
    return sorted(addresses)


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address):
        super().__init__(host, port=443, timeout=30, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        raw = socket.create_connection((self.address, 443), timeout=self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


def validate_image_url(url, allow_hosts, transport=None, resolver=None):
    """Validate bounded public content. transport is an injected trusted test seam.

    transport(url, timeout=30) must return a context-managed binary response with
    status and headers; it must not follow redirects. Default transport pins DNS.
    """
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        allowed = {str(value).lower().rstrip('.') for value in allow_hosts}
        if (parsed.scheme != 'https' or not host or host.lower().rstrip('.') not in allowed
                or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 443) or parsed.fragment
                or any(ord(character) < 33 for character in url)):
            raise ValueError()
    except (TypeError, ValueError):
        raise MediaError('Media URL requires HTTPS, an explicitly allowed host and no credentials or fragment') from None
    addresses = _public_addresses(host, resolver or socket.getaddrinfo)
    connection = None
    try:
        if transport is not None:
            response = transport(url, timeout=30)
        else:
            connection = _PinnedHTTPS(host, addresses[0])
            target = parsed.path or '/'
            if parsed.query:
                target += '?' + parsed.query
            # Never forward an API token, cookies or Authorization to media hosts.
            connection.request('GET', target, headers={'Accept': 'image/jpeg, image/png', 'Accept-Encoding': 'identity'})
            response = connection.getresponse()
        with response:
            if response.status != 200:
                raise MediaError('Media download failed or redirected')
            content_length = response.headers.get('Content-Length')
            if content_length is not None and int(content_length) > MAX_BYTES:
                raise MediaError('Image exceeds 8 MiB')
            if response.headers.get('Content-Encoding', 'identity').lower() != 'identity':
                raise MediaError('Encoded media responses are unsupported')
            data = response.read(MAX_BYTES + 1)
        return _validate_bytes(data)
    except MediaError:
        raise
    except Exception:
        raise MediaError('Media HTTPS fetch or response validation failed') from None
    finally:
        if connection is not None:
            connection.close()
