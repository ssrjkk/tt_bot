import asyncio
import socket
import sys
from datetime import datetime, timedelta, timezone

import pytest
from aiohttp import web

from src.probes import (
    EndpointProber,
    _is_blocked_ip,
    _SafeResolver,
    normalize_endpoint,
    parse_cert_days_left,
)


def test_normalize_endpoint_and_cert_date():
    assert normalize_endpoint("example.com/health") == "https://example.com/health"
    assert normalize_endpoint("  http://api.local/ping ") == "http://api.local/ping"
    assert normalize_endpoint("tcp://10.0.0.1:5432") == "tcp://10.0.0.1:5432"

    with pytest.raises(ValueError):
        normalize_endpoint("   ")
    with pytest.raises(ValueError):
        normalize_endpoint("https://bad host/path")

    future = (datetime.now(timezone.utc) + timedelta(days=45, hours=2)).strftime(
        "%b %d %H:%M:%S %Y GMT"
    )
    assert parse_cert_days_left(future) == 45


def test_is_blocked_ip_variants():
    assert _is_blocked_ip("127.0.0.1") is True
    assert _is_blocked_ip("10.0.0.5") is True
    assert _is_blocked_ip("169.254.169.254") is True
    assert _is_blocked_ip("::1") is True
    assert _is_blocked_ip("::ffff:127.0.0.1") is True
    assert _is_blocked_ip("8.8.8.8") is False


@pytest.mark.asyncio
async def test_ssrf_guard_blocks_loopback_and_private():
    prober = EndpointProber(timeout_sec=2.0, allow_private=False)
    try:
        res_local = await prober.probe("http://127.0.0.1:8080/health")
        assert res_local.ok is False
        assert "SSRF guard" in res_local.details

        res_v6 = await prober.probe("http://[::1]:8080/health")
        assert res_v6.ok is False
        assert "SSRF guard" in res_v6.details
    finally:
        await prober.close()


@pytest.mark.asyncio
async def test_ssrf_guard_blocks_dns_to_private_and_empty_dns(monkeypatch):
    loop = asyncio.get_running_loop()

    async def fake_private(host, port, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.42", 0))]

    monkeypatch.setattr(loop, "getaddrinfo", fake_private)
    prober = EndpointProber(timeout_sec=2.0, allow_private=False)
    try:
        res = await prober.probe("https://internal.corp.example/health")
        assert res.ok is False
        assert "SSRF guard" in res.details

        async def fake_empty(host, port, **kwargs):
            return []

        monkeypatch.setattr(loop, "getaddrinfo", fake_empty)
        res_empty = await prober.probe("https://nodns.example/health")
        assert res_empty.ok is False
        assert "DNS" in res_empty.details
    finally:
        await prober.close()


@pytest.mark.asyncio
async def test_safe_resolver_blocks_private_records(monkeypatch):
    resolver = _SafeResolver(allow_private=False)

    async def fake_parent_resolve(self, host, port=0, family=socket.AF_UNSPEC):
        return [{"host": "127.0.0.1", "port": port, "hostname": host}]

    monkeypatch.setattr("aiohttp.resolver.DefaultResolver.resolve", fake_parent_resolve)
    with pytest.raises(PermissionError, match="SSRF guard"):
        await resolver.resolve("rebind.example", 80)

    resolver_priv = _SafeResolver(allow_private=True)
    recs = await resolver_priv.resolve("rebind.example", 80)
    assert recs[0]["host"] == "127.0.0.1"


@pytest.mark.asyncio
async def test_unsupported_scheme_and_bad_tcp_port():
    prober = EndpointProber(timeout_sec=2.0, allow_private=True)
    try:
        bad_scheme = await prober.probe("ftp://127.0.0.1/file")
        assert bad_scheme.ok is False
        assert "Неподдерживаемый формат" in bad_scheme.details

        no_port = await prober.probe("tcp://127.0.0.1")
        assert no_port.ok is False
        assert "порт" in no_port.details
    finally:
        await prober.close()


@pytest.mark.asyncio
async def test_real_http_probe_ok_body_fail_and_timeout():
    async def handle_ok(_request):
        return web.Response(status=200, text='{"status":"healthy"}')

    async def handle_created(_request):
        return web.Response(status=201, text="created")

    async def handle_fail(_request):
        return web.Response(status=503, text="unavailable")

    async def handle_redirect(_request):
        raise web.HTTPFound(location="http://127.0.0.1/secret")

    async def handle_slow(_request):
        await asyncio.sleep(0.5)
        return web.Response(status=200, text="late")

    app = web.Application()
    app.router.add_get("/ok", handle_ok)
    app.router.add_get("/created", handle_created)
    app.router.add_get("/fail", handle_fail)
    app.router.add_get("/redir", handle_redirect)
    app.router.add_get("/slow", handle_slow)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    prober = EndpointProber(timeout_sec=0.2, allow_private=True)
    try:
        r_ok = await prober.probe(
            f"http://127.0.0.1:{port}/ok", expected_text="healthy"
        )
        assert r_ok.ok is True
        assert r_ok.status_code == 200

        r_bad_body = await prober.probe(
            f"http://127.0.0.1:{port}/ok", expected_text="MISSING_TOKEN"
        )
        assert r_bad_body.ok is False
        assert "не найдена строка" in r_bad_body.details

        r_exact = await prober.probe(
            f"http://127.0.0.1:{port}/created", expected_status=201
        )
        assert r_exact.ok is True

        r_fail = await prober.probe(f"http://127.0.0.1:{port}/fail")
        assert r_fail.ok is False
        assert r_fail.status_code == 503

        r_redir = await prober.probe(f"http://127.0.0.1:{port}/redir")
        assert r_redir.ok is False
        assert r_redir.status_code == 302

        r_slow = await prober.probe(f"http://127.0.0.1:{port}/slow")
        assert r_slow.ok is False
        assert "Timeout" in r_slow.details
    finally:
        await prober.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_real_tcp_probe_success_and_refused():
    async def handle_client(_reader, writer):
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(handle_client, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    prober = EndpointProber(timeout_sec=1.0, allow_private=True)
    try:
        res_ok = await prober.probe(f"tcp://127.0.0.1:{port}")
        assert res_ok.ok is True
        assert "TCP OK" in res_ok.details
    finally:
        server.close()
        await server.wait_closed()

    try:
        res_closed = await prober.probe(f"tcp://127.0.0.1:{port}")
        assert res_closed.ok is False
        if sys.platform.startswith("win"):
            # Windows drops SYNs to a closed loopback port instead of replying RST,
            # so a refused TCP probe surfaces as TimeoutError here, not OSError.
            assert "Timeout" in res_closed.details
        else:
            assert "Ошибка сети" in res_closed.details
    finally:
        await prober.close()
