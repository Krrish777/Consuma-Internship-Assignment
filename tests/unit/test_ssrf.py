"""Callback URL guard (pure; getaddrinfo mocked, no network).

A client-supplied callback_url handed to httpx is an SSRF vector (internal
services, cloud metadata endpoints). is_allowed must reject private/loopback/
link-local/reserved/multicast/unspecified targets, honor the allowlist, and check
EVERY resolved address (a public hostname can still point at a private IP).
"""

from __future__ import annotations

import socket
from unittest.mock import patch

from worker.ssrf import is_allowed


def _gai(*ips: str) -> list[tuple[int, int, int, str, tuple[str, int]]]:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in ips]


def test_metadata_endpoint_blocked() -> None:
    with patch("worker.ssrf.socket.getaddrinfo", return_value=_gai("169.254.169.254")):
        assert is_allowed("http://169.254.169.254/latest/meta-data/", ()) is False


def test_localhost_blocked() -> None:
    with patch("worker.ssrf.socket.getaddrinfo", return_value=_gai("127.0.0.1")):
        assert is_allowed("http://localhost/hook", ()) is False


def test_private_host_blocked() -> None:
    with patch("worker.ssrf.socket.getaddrinfo", return_value=_gai("10.0.0.5")):
        assert is_allowed("http://10.0.0.5/hook", ()) is False


def test_unspecified_address_blocked() -> None:
    with patch("worker.ssrf.socket.getaddrinfo", return_value=_gai("0.0.0.0")):
        assert is_allowed("http://0.0.0.0/", ()) is False


def test_allowlisted_public_host_allowed() -> None:
    with patch("worker.ssrf.socket.getaddrinfo", return_value=_gai("93.184.216.34")):
        assert is_allowed("https://api.example.com/hook", ("api.example.com",)) is True


def test_non_allowlisted_public_host_blocked_when_allowlist_set() -> None:
    with patch("worker.ssrf.socket.getaddrinfo", return_value=_gai("93.184.216.34")):
        assert is_allowed("https://evil.com/hook", ("api.example.com",)) is False


def test_dns_rebinding_any_private_record_blocks() -> None:
    # Host resolves to a public AND a private IP → must be rejected (check ALL records).
    with patch("worker.ssrf.socket.getaddrinfo", return_value=_gai("93.184.216.34", "10.1.2.3")):
        assert is_allowed("https://api.example.com/hook", ("api.example.com",)) is False


def test_unresolvable_host_blocked() -> None:
    with patch("worker.ssrf.socket.getaddrinfo", side_effect=socket.gaierror):
        assert is_allowed("http://nonexistent.invalid/", ()) is False


def test_url_without_host_blocked() -> None:
    assert is_allowed("not-a-url", ()) is False
