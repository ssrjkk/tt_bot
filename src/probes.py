import asyncio
import ipaddress
import socket
import ssl
from datetime import datetime, timezone
from typing import Any, List, Optional
from urllib.parse import urlparse

import aiohttp
from aiohttp.resolver import DefaultResolver

from src.models import ProbeResult

ALLOWED_SCHEMES = {"http", "https", "tcp"}
MAX_BODY_INSPECT_BYTES = 65536


def normalize_endpoint(raw: str) -> str:
    endpoint = raw.strip()
    if not endpoint or any(ch.isspace() for ch in endpoint):
        raise ValueError("Некорректный адрес эндпоинта")
    if "://" not in endpoint:
        return f"https://{endpoint}"
    return endpoint


def _is_blocked_ip(ip_str: str) -> bool:
    ip = ipaddress.ip_address(ip_str)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return any(
        (
            ip.is_private,
            ip.is_loopback,
            ip.is_link_local,
            ip.is_multicast,
            ip.is_reserved,
            ip.is_unspecified,
        )
    )


def parse_cert_days_left(not_after: str) -> int:
    expiry = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(
        tzinfo=timezone.utc
    )
    delta = expiry - datetime.now(timezone.utc)
    return int(delta.total_seconds() // 86400)


async def _resolve_safe_ips(host: str, allow_private: bool) -> List[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    if not infos:
        raise ValueError(f"DNS не вернул адресов для {host}")
    ips: List[str] = []
    for info in infos:
        ip_str = info[4][0]
        if not allow_private and _is_blocked_ip(ip_str):
            raise PermissionError(
                f"Доступ к приватному/локальному адресу {ip_str} запрещён (SSRF guard)"
            )
        if ip_str not in ips:
            ips.append(ip_str)
    return ips


class _SafeResolver(DefaultResolver):
    """DNS-резолвер для aiohttp с проверкой IP на этапе подключения."""

    def __init__(self, allow_private: bool = False) -> None:
        super().__init__()
        self.allow_private = allow_private

    async def resolve(
        self, host: str, port: int = 0, family: int = socket.AF_UNSPEC
    ) -> List[dict[str, Any]]:
        records = await super().resolve(host, port, family)
        if not self.allow_private:
            for rec in records:
                ip_str = str(rec["host"])
                if _is_blocked_ip(ip_str):
                    raise PermissionError(
                        f"Доступ к приватному/локальному адресу {ip_str} "
                        "запрещён (SSRF guard)"
                    )
        return records


class EndpointProber:
    """Асинхронный HTTP/HTTPS, SSL и TCP пробник с SSRF-защитой."""

    def __init__(
        self,
        timeout_sec: float = 10.0,
        allow_private: bool = False,
        user_agent: str = "tt_bot-monitor/2.1",
    ) -> None:
        self.timeout_sec = timeout_sec
        self.allow_private = allow_private
        self.user_agent = user_agent
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.timeout_sec)
            connector = aiohttp.TCPConnector(
                resolver=_SafeResolver(allow_private=self.allow_private),
                ttl_dns_cache=0,
            )
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                headers={"User-Agent": self.user_agent},
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def probe(
        self,
        raw_endpoint: str,
        expected_status: int = 200,
        expected_text: str = "",
        ssl_min_days: int = 14,
    ) -> ProbeResult:
        try:
            url = normalize_endpoint(raw_endpoint)
            parsed = urlparse(url)
            if parsed.scheme not in ALLOWED_SCHEMES or not parsed.hostname:
                return ProbeResult(
                    ok=False,
                    details=f"Неподдерживаемый формат адреса: {raw_endpoint}",
                )
            ips = await _resolve_safe_ips(parsed.hostname, self.allow_private)
            if parsed.scheme == "tcp":
                return await self._probe_tcp(parsed.hostname, ips[0], parsed.port)
            return await self._probe_http(
                url,
                parsed.scheme,
                parsed.hostname,
                ips[0],
                parsed.port or (443 if parsed.scheme == "https" else 80),
                expected_status,
                expected_text,
                ssl_min_days,
            )
        except (ValueError, PermissionError) as exc:
            return ProbeResult(ok=False, details=str(exc))
        except (asyncio.TimeoutError, TimeoutError):
            return ProbeResult(
                ok=False, details=f"Timeout после {self.timeout_sec:.1f} с"
            )
        except (aiohttp.ClientError, OSError) as exc:
            return ProbeResult(ok=False, details=f"Ошибка сети: {exc}")

    async def _probe_tcp(
        self, host: str, resolved_ip: str, port: Optional[int]
    ) -> ProbeResult:
        if not port or not (1 <= port <= 65535):
            return ProbeResult(
                ok=False,
                details=(
                    "Для tcp:// укажите корректный порт " "(например tcp://host:5432)"
                ),
            )
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(resolved_ip, port),
            timeout=self.timeout_sec,
        )
        writer.close()
        await writer.wait_closed()
        return ProbeResult(ok=True, details=f"TCP OK ({host}:{port})")

    async def _inspect_ssl_days(
        self, host: str, resolved_ip: str, port: int
    ) -> Optional[int]:
        try:
            ctx = ssl.create_default_context()
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(
                    resolved_ip, port, ssl=ctx, server_hostname=host
                ),
                timeout=min(5.0, self.timeout_sec),
            )
            try:
                ssl_obj = writer.get_extra_info("ssl_object")
                cert = ssl_obj.getpeercert() if ssl_obj else None
                not_after = cert.get("notAfter") if cert else None
                return parse_cert_days_left(not_after) if not_after else None
            finally:
                writer.close()
                await writer.wait_closed()
        except Exception:
            return None

    @staticmethod
    def _status_matches(status: int, expected_status: int) -> bool:
        if expected_status != 200:
            return status == expected_status
        return 200 <= status < 300

    async def _verify_body(
        self, resp: aiohttp.ClientResponse, expected_text: str, details: str
    ) -> tuple[bool, str]:
        if not expected_text:
            return True, details
        chunk = await resp.content.read(MAX_BODY_INSPECT_BYTES)
        body = chunk.decode("utf-8", errors="ignore")
        if expected_text in body:
            return True, details
        return False, f"{details} (не найдена строка «{expected_text[:30]}»)"

    async def _verify_ssl(
        self,
        scheme: str,
        host: str,
        resolved_ip: str,
        port: int,
        ssl_min_days: int,
        details: str,
    ) -> tuple[bool, Optional[int], str]:
        if scheme != "https" or ssl_min_days <= 0:
            return True, None, details
        ssl_days = await self._inspect_ssl_days(host, resolved_ip, port)
        if ssl_days is not None and ssl_days < ssl_min_days:
            return False, ssl_days, f"{details} · SSL истекает через {ssl_days} дн."
        return True, ssl_days, details

    async def _probe_http(
        self,
        url: str,
        scheme: str,
        host: str,
        resolved_ip: str,
        port: int,
        expected_status: int,
        expected_text: str,
        ssl_min_days: int,
    ) -> ProbeResult:
        session = await self._get_session()
        async with session.get(url, allow_redirects=False) as resp:
            status = resp.status
            ok = self._status_matches(status, expected_status)
            details = f"HTTP {status} {resp.reason or ''}".strip()
            if ok:
                ok, details = await self._verify_body(resp, expected_text, details)

        ssl_days: Optional[int] = None
        if ok:
            ok, ssl_days, details = await self._verify_ssl(
                scheme, host, resolved_ip, port, ssl_min_days, details
            )

        return ProbeResult(
            ok=ok, details=details, status_code=status, ssl_days_left=ssl_days
        )
