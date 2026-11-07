"""Bounded public-page reads with DNS pinning; no login, cookies or challenge bypass."""

import http.client
import ipaddress
import socket
import ssl
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import certifi

from company.schemas.intake import PageObservation

MAX_PAGE_BYTES = 256 * 1024
MAX_EXCERPT = 12000


class PublicPageBlocked(ValueError):
    pass


def public_endpoint(url: str) -> tuple[str, int, str]:
    parts = urlsplit(url)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username
        or parts.password
    ):
        raise PublicPageBlocked()
    port = parts.port or (443 if parts.scheme == "https" else 80)
    if port not in {80, 443}:
        raise PublicPageBlocked()
    addresses = socket.getaddrinfo(parts.hostname, port, type=socket.SOCK_STREAM)
    if not addresses:
        raise PublicPageBlocked()
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global or ip.is_multicast or ip.is_reserved:
            raise PublicPageBlocked()
        if isinstance(ip, ipaddress.IPv6Address) and (
            ip.is_site_local or ip.sixtofour or ip.teredo
        ):
            raise PublicPageBlocked()
    return parts.hostname, port, addresses[0][4][0]


class PageText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ignored = 0
        self.in_title = False
        self.title, self.parts = [], []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "svg"}:
            self.ignored += 1
        if tag == "title":
            self.in_title = True
        if tag == "meta":
            attributes = dict(attrs)
            if attributes.get("name", "").lower() == "description" or attributes.get(
                "property", ""
            ).lower() in {"og:title", "og:description"}:
                self.parts.append(attributes.get("content", ""))

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript", "svg"}:
            self.ignored = max(0, self.ignored - 1)
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title.append(data)
        if not self.ignored and data.strip():
            self.parts.append(data.strip())


def _request(url: str, *, timeout: float):
    host, port, address = public_endpoint(url)
    parts = urlsplit(url)
    connection = http.client.HTTPConnection(host, port, timeout=timeout)
    raw = socket.create_connection((address, port), timeout=timeout)
    try:
        connection.sock = (
            ssl.create_default_context(cafile=certifi.where()).wrap_socket(
                raw, server_hostname=host
            )
            if parts.scheme == "https"
            else raw
        )
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        connection.request(
            "GET",
            path,
            headers={
                "Host": parts.netloc,
                "User-Agent": "ListingStudio/1.0 (product-reference-reader)",
                "Accept": "text/html,text/plain",
                "Accept-Encoding": "identity",
            },
        )
        response = connection.getresponse()
        headers = {key.lower(): value for key, value in response.getheaders()}
        data = response.read(MAX_PAGE_BYTES + 1)
        return response.status, headers, data
    finally:
        connection.close()
        raw.close()


def fetch_product_page(url: str | None) -> PageObservation | None:
    if not url:
        return None
    observed = PageObservation(
        url=url, status="unavailable", retrieved_at=datetime.now(UTC).isoformat()
    )
    current = url
    try:
        for _ in range(4):
            status, headers, data = _request(current, timeout=4)
            if status in {301, 302, 303, 307, 308}:
                if not headers.get("location"):
                    return observed
                current = urljoin(current, headers["location"])
                continue
            if status in {401, 403, 429}:
                return observed.model_copy(update={"status": "restricted"})
            if status != 200:
                return observed
            if len(data) > MAX_PAGE_BYTES:
                return observed.model_copy(update={"status": "too_large"})
            if headers.get("content-encoding", "identity").lower() not in {"", "identity"}:
                return observed.model_copy(update={"status": "unsupported"})
            content_type = headers.get("content-type", "").lower()
            if not any(
                kind in content_type
                for kind in ["text/html", "text/plain", "application/xhtml+xml"]
            ):
                return observed.model_copy(update={"status": "unsupported"})
            encoding = "utf-8"
            if "charset=" in content_type:
                encoding = content_type.split("charset=", 1)[1].split(";", 1)[0].strip(' "')
            try:
                html = data.decode(encoding, errors="replace")
            except LookupError:
                html = data.decode("utf-8", errors="replace")
            parser = PageText()
            parser.feed(html)
            excerpt = " ".join(parser.parts)[:MAX_EXCERPT]
            title = " ".join(parser.title)[:500]
            challenge = any(
                word in (title + " " + excerpt[:2000]).lower()
                for word in ["captcha", "请完成验证", "访问验证", "安全验证", "登录后查看"]
            )
            return observed.model_copy(
                update={
                    "url": current,
                    "status": "restricted" if challenge else "ok" if excerpt else "empty",
                    "title": title,
                    "excerpt": "" if challenge else excerpt,
                }
            )
        return observed.model_copy(update={"status": "redirect_limit"})
    except PublicPageBlocked:
        return observed.model_copy(update={"status": "blocked"})
    except Exception:
        # Network exceptions and page contents may contain private query parameters.
        return observed
