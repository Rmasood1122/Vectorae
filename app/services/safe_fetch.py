"""Fetch ONE public web page a user named, safely (F06 Quickstart).

WHY THIS EXISTS
Quickstart takes a website URL from the user and reads that page. A URL from a
user is an instruction to make an outbound request from inside our network, so
it is the textbook SSRF surface: http://169.254.169.254/ (cloud metadata),
http://localhost:6379/ (Redis), http://10.0.0.5/admin. Nothing in the codebase
fetched arbitrary user URLs before -- market_intel only reaches fixed hosts and
follows redirects anywhere, which is fine there and wrong here. The closest
guard is outbound webhooks (app/services/webhook_delivery.py), and its address
classification (`_non_public`) is reused below so the two can never disagree
about what "not public" means.

THE RULES
  * https only, port 443 only. A bare host or http:// is UPGRADED to https://
    (a business homepage serves https; a plain-http fetch would also let any
    on-path party rewrite the text the ICP is derived from).
  * No credentials in the URL; no localhost / .localhost / .local / .internal
    / single-label host; no IP literal that is not globally routable.
  * Every hop re-resolves the host and is refused if ANY address it resolves
    to is non-public (private, loopback, link-local -- the metadata service --
    reserved, multicast, shared/CGNAT ...).
  * Redirects are followed BY HAND, at most MAX_REDIRECTS, each target
    validated and resolved exactly like the first URL.
  * TIMEOUT_SECONDS for the whole fetch; at most MAX_BYTES read (truncated, not
    failed); text/html or text/plain only.

RESIDUAL RISK (documented, as in webhook_delivery): there is a DNS-rebinding
window between our resolution and httpx's own. Closing it fully needs an
egress proxy that enforces the same rules at connect time.

The text this returns is UNTRUSTED. Callers must delimit it with
app/services/prompt_safety.py before it goes anywhere near a prompt.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, urlunparse

import httpx

from app.services.webhook_delivery import _non_public

logger = logging.getLogger(__name__)

MAX_URL_LENGTH = 2048
MAX_REDIRECTS = 3
MAX_BYTES = 512_000
TIMEOUT_SECONDS = 8.0
MAX_TEXT_CHARS = 20_000
USER_AGENT = "Vectorae/1.0 quickstart (+https://calendarharvest.com)"
_ALLOWED_TYPES = ("text/html", "text/plain", "application/xhtml+xml")


class UnsafeURL(ValueError):
    """The URL (or a redirect it led to) may not be fetched. Message is safe to
    show the user."""


class FetchFailed(RuntimeError):
    """The page could not be read (network error, HTTP error, wrong type)."""


@dataclass
class FetchResult:
    url: str            # the URL as given (normalised)
    final_url: str      # after redirects
    status_code: int
    content_type: str
    bytes_read: int
    truncated: bool
    title: str
    text: str           # visible text, whitespace-collapsed, <= MAX_TEXT_CHARS

    def meta(self) -> dict:
        return {"final_url": self.final_url, "status_code": self.status_code,
                "content_type": self.content_type, "bytes": self.bytes_read,
                "truncated": self.truncated, "title": self.title[:300]}


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def validate_url(raw: str | None) -> str:
    """Normalise and check a user-supplied URL WITHOUT touching the network.

    Returns the https URL to fetch. Raises UnsafeURL with a user-facing reason.
    """
    url = (raw or "").strip()
    if not url:
        raise UnsafeURL("enter your website address")
    if len(url) > MAX_URL_LENGTH:
        raise UnsafeURL("that address is too long")
    if any(c.isspace() for c in url) or any(ord(c) < 32 for c in url):
        raise UnsafeURL("that address contains spaces or control characters")
    if "://" not in url:
        url = "https://" + url
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme == "http":
        parsed = parsed._replace(scheme="https")
    elif scheme != "https":
        raise UnsafeURL("only web addresses (https://) can be read")
    if parsed.username or parsed.password or "@" in (parsed.netloc or ""):
        raise UnsafeURL("addresses with a user name or password are not allowed")
    try:
        port = parsed.port
    except ValueError:
        raise UnsafeURL("that address has an invalid port") from None
    if port not in (None, 443):
        raise UnsafeURL("only the standard https port (443) is allowed")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        raise UnsafeURL("that address has no host name")
    literal = None
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        if _non_public(str(literal)):
            raise UnsafeURL("private, loopback and link-local addresses are not allowed")
    else:
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal",
                                                  ".lan", ".home.arpa")):
            raise UnsafeURL("internal host names are not allowed")
        if "." not in host:
            raise UnsafeURL("enter a full domain name, like example.com")
        if not host.isascii():
            try:
                host = host.encode("idna").decode("ascii")
            except UnicodeError:
                raise UnsafeURL("that host name is not valid") from None
        if not re.fullmatch(r"[a-z0-9.-]+", host):
            raise UnsafeURL("that host name is not valid")
    netloc = host if literal is None or literal.version == 4 else f"[{literal}]"
    path = parsed.path or "/"
    return urlunparse(("https", netloc, path, "", parsed.query, ""))


def _resolve(host: str) -> list[str]:
    """Every address `host` resolves to. Tests monkeypatch this."""
    return list({info[4][0] for info in
                 socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)})


def check_destination(url: str) -> None:
    """Resolve the URL's host and refuse unless EVERY address is public."""
    host = urlparse(url).hostname or ""
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        addresses = [str(literal)]
    else:
        try:
            addresses = _resolve(host)
        except OSError as exc:
            raise FetchFailed(f"could not find {host}") from exc
    if not addresses:
        raise FetchFailed(f"could not find {host}")
    if any(_non_public(a) for a in addresses):
        raise UnsafeURL("that address points into a private network")


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------


def _http() -> httpx.Client:
    """Factory tests monkeypatch. Redirects are NEVER followed by httpx."""
    return httpx.Client(timeout=httpx.Timeout(TIMEOUT_SECONDS, connect=5.0),
                        follow_redirects=False,
                        headers={"User-Agent": USER_AGENT,
                                 "Accept": "text/html,text/plain;q=0.9"})


def fetch_page(raw_url: str, *, max_bytes: int = MAX_BYTES,
               max_redirects: int = MAX_REDIRECTS) -> FetchResult:
    """GET one public page and return its visible text. See the module docstring."""
    start = validate_url(raw_url)
    deadline = time.monotonic() + TIMEOUT_SECONDS
    current = start
    with _http() as client:
        for hop in range(max_redirects + 1):
            current = validate_url(current)
            check_destination(current)
            if time.monotonic() > deadline:
                raise FetchFailed("the website took too long to answer")
            try:
                with client.stream("GET", current) as resp:
                    if resp.status_code in (301, 302, 303, 307, 308):
                        location = resp.headers.get("location")
                        if not location:
                            raise FetchFailed("the website sent a redirect without a target")
                        if hop >= max_redirects:
                            raise FetchFailed("the website redirected too many times")
                        current = urljoin(current, location)
                        continue
                    if resp.status_code >= 400:
                        raise FetchFailed(f"the website answered HTTP {resp.status_code}")
                    content_type = (resp.headers.get("content-type") or "").split(";")[0]
                    content_type = content_type.strip().lower()
                    if content_type and not content_type.startswith(_ALLOWED_TYPES):
                        raise FetchFailed(f"the address is not a web page ({content_type})")
                    chunks: list[bytes] = []
                    read = 0
                    truncated = False
                    for chunk in resp.iter_bytes():
                        if time.monotonic() > deadline:
                            truncated = True
                            break
                        room = max_bytes - read
                        if len(chunk) >= room:
                            chunks.append(chunk[:room])
                            read += room
                            truncated = True
                            break
                        chunks.append(chunk)
                        read += len(chunk)
                    raw = b"".join(chunks)
                    encoding = resp.encoding or "utf-8"
                    try:
                        body = raw.decode(encoding, errors="replace")
                    except LookupError:
                        body = raw.decode("utf-8", errors="replace")
                    if content_type == "text/plain":
                        title, text = "", _collapse(body)
                    else:
                        title, text = html_to_text(body)
                    return FetchResult(
                        url=start, final_url=current, status_code=resp.status_code,
                        content_type=content_type or "unknown", bytes_read=read,
                        truncated=truncated, title=title, text=text[:MAX_TEXT_CHARS])
            except (UnsafeURL, FetchFailed):
                raise
            except httpx.HTTPError as exc:
                raise FetchFailed(f"could not read the website ({type(exc).__name__})") from exc
    raise FetchFailed("the website redirected too many times")


# --------------------------------------------------------------------------
# HTML -> visible text
# --------------------------------------------------------------------------

_SKIP = {"script", "style", "noscript", "svg", "template", "iframe", "head", "canvas"}
_BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "section",
          "article", "header", "footer", "tr", "td", "th", "ul", "ol", "nav", "main"}


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self.description = ""
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "title":
            self._in_title = True
            return
        if tag == "meta":
            values = {k.lower(): (v or "") for k, v in attrs}
            if values.get("name", "").lower() in ("description", "og:description") \
                    or values.get("property", "").lower() == "og:description":
                if not self.description:
                    self.description = values.get("content", "")[:500]
            return
        if tag in _SKIP:
            self._skip_depth += 1
            return
        if tag in _BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
            return
        if tag in _SKIP and self._skip_depth:
            self._skip_depth -= 1
            return
        if tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title_parts.append(data)
            return
        if self._skip_depth:
            return
        self.parts.append(data)


def _collapse(text: str) -> str:
    lines = [" ".join(line.split()) for line in (text or "").splitlines()]
    return "\n".join(line for line in lines if line)


def html_to_text(html: str) -> tuple[str, str]:
    """(title, visible text). Scripts, styles and other non-content dropped.

    `<title>` sits inside `<head>`, which is otherwise skipped, so the title is
    captured separately; the meta description is prepended to the text because
    on a JavaScript-rendered site it is often the only real copy there is."""
    parser = _TextParser()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:  # noqa: BLE001 -- malformed markup degrades to what was parsed
        logger.info("safe_fetch: HTML parse stopped early")
    title = " ".join("".join(parser.title_parts).split())[:300]
    text = _collapse("".join(parser.parts))
    if parser.description:
        text = _collapse(parser.description) + "\n" + text
    return title, text
