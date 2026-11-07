"""Public reference reads are bounded, pin validated addresses and disclose failures honestly."""

import socket

import pytest

from company.providers import product_page as page


def address(ip):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))]


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "http://user:pass@example.com", "http://example.com:8000/a"]
)
def test_non_public_protocols_credentials_and_ports_are_rejected(url):
    with pytest.raises(page.PublicPageBlocked):
        page.public_endpoint(url)


@pytest.mark.parametrize(
    "ip", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "192.168.1.1", "224.0.0.1", "0.0.0.0"]
)
def test_private_loopback_metadata_and_multicast_addresses_are_rejected(monkeypatch, ip):
    monkeypatch.setattr(page.socket, "getaddrinfo", lambda *a, **k: address(ip))
    with pytest.raises(page.PublicPageBlocked):
        page.public_endpoint("https://example.com/product")


def test_public_address_is_returned_for_pinned_connection(monkeypatch):
    monkeypatch.setattr(page.socket, "getaddrinfo", lambda *a, **k: address("93.184.216.34"))
    assert page.public_endpoint("https://example.com/product") == (
        "example.com",
        443,
        "93.184.216.34",
    )
    monkeypatch.setattr(
        page.socket, "getaddrinfo", lambda *a, **k: address("93.184.216.34") + address("127.0.0.1")
    )
    with pytest.raises(page.PublicPageBlocked):
        page.public_endpoint("https://example.com/product")


def test_html_text_has_title_description_and_no_script_or_styles(monkeypatch):
    html = (
        b'<title>Blue jersey</title><meta name="description" content="White stripes">'
        b"<script>PRIVATE_SCRIPT</script><style>PRIVATE_STYLE</style>"
        b"<main>Player themed shirt</main>"
    )
    monkeypatch.setattr(
        page, "_request", lambda *a, **k: (200, {"content-type": "text/html; charset=utf-8"}, html)
    )
    result = page.fetch_product_page("https://example.com/product")
    assert result.status == "ok" and result.title == "Blue jersey"
    assert "White stripes" in result.excerpt and "Player themed" in result.excerpt
    assert "PRIVATE" not in result.excerpt


@pytest.mark.parametrize(
    "status,headers,body,expected",
    [
        (403, {}, b"login", "restricted"),
        (200, {"content-type": "text/html"}, b"<title>captcha</title>secret", "restricted"),
        (200, {"content-type": "application/pdf"}, b"pdf", "unsupported"),
        (200, {"content-type": "text/html"}, b"x" * (page.MAX_PAGE_BYTES + 1), "too_large"),
        (
            200,
            {"content-type": "text/html", "content-encoding": "gzip"},
            b"compressed",
            "unsupported",
        ),
    ],
)
def test_failures_never_become_product_page_evidence(monkeypatch, status, headers, body, expected):
    monkeypatch.setattr(page, "_request", lambda *a, **k: (status, headers, body))
    result = page.fetch_product_page("https://example.com/product")
    assert result.status == expected and result.excerpt == ""


def test_redirects_revalidate_destination_and_are_bounded(monkeypatch):
    calls = []

    def request(url, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            return 302, {"location": "http://127.0.0.1/private"}, b""
        raise page.PublicPageBlocked()

    monkeypatch.setattr(page, "_request", request)
    assert page.fetch_product_page("https://example.com/product").status == "blocked"
    assert len(calls) == 2
    calls.clear()

    def loop(url, **kwargs):
        calls.append(url)
        return 302, {"location": "/again"}, b""

    monkeypatch.setattr(page, "_request", loop)
    assert page.fetch_product_page("https://example.com/product").status == "redirect_limit"
    assert len(calls) == 4


def test_network_error_is_safe_and_does_not_echo_details(monkeypatch):
    def failed(*args, **kwargs):
        raise OSError("PRIVATE-NETWORK-MARKER")

    monkeypatch.setattr(page, "_request", failed)
    result = page.fetch_product_page("https://example.com/product")
    assert (
        result.status == "unavailable" and "PRIVATE-NETWORK-MARKER" not in result.model_dump_json()
    )
