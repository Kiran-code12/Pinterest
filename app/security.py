"""Password hashing, CSRF, rate limiting and URL validation (SSRF-safe)."""
import hashlib
import hmac
import ipaddress
import re
import secrets
import socket
import time
from collections import defaultdict, deque
from urllib.parse import urlsplit

# ---- passwords (stdlib scrypt: no extra dependency) -------------------------------------------------

_N, _R, _P = 2 ** 14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        calc = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(calc, bytes.fromhex(digest))
    except (ValueError, TypeError):
        return False


# ---- CSRF -------------------------------------------------------------------------------------------

def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_ok(expected: str | None, provided: str | None) -> bool:
    return bool(expected and provided) and hmac.compare_digest(expected, provided)


# ---- rate limiting (in-memory sliding window; fine for a single-process personal tool) --------------

class RateLimiter:
    def __init__(self):
        self._hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str, limit: int, window: float = 60.0) -> bool:
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


# ---- URL validation ---------------------------------------------------------------------------------

MAX_URL_LEN = 2048
_CTRL = re.compile(r"[\x00-\x20\x7f]")


class UnsafeURL(ValueError):
    pass


def validate_http_url(url: str, *, require_https: bool = False) -> str:
    """Syntax-only check for URLs we store/display (affiliate links, product pages)."""
    url = (url or "").strip()
    if not url or len(url) > MAX_URL_LEN or _CTRL.search(url):
        raise UnsafeURL("URL is empty, too long or contains control/space characters")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or (require_https and parts.scheme != "https"):
        raise UnsafeURL("Only http(s) URLs are allowed" if not require_https else "Only https URLs are allowed")
    if not parts.hostname or parts.username or parts.password:
        raise UnsafeURL("URL needs a host and must not contain credentials")
    return url


def _is_public_ip(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return not (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast
                or addr.is_reserved or addr.is_unspecified)


def validate_fetch_url(url: str, *, allow_private: bool = False, resolver=socket.getaddrinfo) -> str:
    """URLs we will actually download from: must resolve to public addresses only (SSRF guard)."""
    url = validate_http_url(url)
    if allow_private:
        return url
    host = urlsplit(url).hostname
    try:
        infos = resolver(host, None)
    except OSError as ex:
        raise UnsafeURL("Host could not be resolved") from ex
    if not infos:
        raise UnsafeURL("Host could not be resolved")
    for info in infos:
        if not _is_public_ip(info[4][0]):
            raise UnsafeURL("URL points to a non-public address")
    return url
