"""Callback URL guard.

A client-supplied ``callback_url`` POSTed by the webhook is a Server-Side
Request Forgery vector: a caller could aim it at internal services or a cloud
metadata endpoint (169.254.169.254). :func:`is_allowed` is the gate.

Defence in depth:
  * **Allowlist** — if ``WEBHOOK_ALLOWLIST`` is non-empty, the host must be in it.
  * **Resolve and check EVERY address** — a public hostname can still resolve to a
    private IP (DNS rebinding), so we reject if ANY resolved A/AAAA record is
    private / loopback / link-local / reserved / multicast / unspecified.
  * Pairs with ``follow_redirects=False`` + a hard timeout in the caller (W5b) so
    every hop is controlled.

Never trust the hostname string alone — the IP check is what actually protects us.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from core.infra.logging import get_logger

log = get_logger("worker.ssrf")


def _is_blocked_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True  # unparseable → treat as unsafe
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def is_allowed(url: str, allowlist: tuple[str, ...]) -> bool:
    """True iff ``url`` is safe to POST to (allowlisted + resolves only to public IPs)."""
    host = urlparse(url).hostname
    if not host:
        log.warning("ssrf: url has no host: %r", url)
        return False

    if allowlist and host not in allowlist:
        log.warning("ssrf: host %r not in allowlist", host)
        return False

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        log.warning("ssrf: host %r does not resolve", host)
        return False

    for info in infos:
        ip = str(info[4][0])
        if _is_blocked_ip(ip):
            log.warning("ssrf: host %r resolves to blocked address %s", host, ip)
            return False
    return True
