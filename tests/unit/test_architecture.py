"""Architectural boundary tests (harness note 12) — enforce SPEC §3 layering MECHANICALLY.

Agents copy whatever import patterns already exist, so the CLAUDE.md MUST/MUST-NOT rules are
turned into checks that fail with what/why/fix guidance. Pure static analysis (ast), no Docker,
so it runs in the L1/L2 gate (`make check`) and the commit gate. New boundary violations found
in review should be promoted into a new assertion here (review-feedback promotion).
"""

from __future__ import annotations

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_CORE_SRC = _ROOT / "packages" / "core" / "src"
_GATEWAY_SRC = _ROOT / "services" / "gateway" / "src"
_WORKER_SRC = _ROOT / "services" / "worker" / "src"

# I/O libraries that must never appear in the pure domain layer.
_IO_LIBS = {"sqlalchemy", "asyncpg", "aio_pika", "redis", "minio", "psycopg", "httpx", "boto3"}
# Managed orchestrators banned by the spec (raw broker choreography only).
_BANNED_ORCHESTRATORS = {
    "celery",
    "taskiq",
    "arq",
    "rq",
    "flower",
    "dramatiq",
    "prefect",
    "airflow",
    "temporalio",
}


def _modules(path: Path) -> set[str]:
    """Full dotted module names imported by a .py file (absolute imports only)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            mods.add(node.module)
    return mods


def _py_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py"))


def test_gateway_never_imports_worker() -> None:
    for f in _py_files(_GATEWAY_SRC):
        offenders = {m for m in _modules(f) if m.split(".")[0] == "worker"}
        assert not offenders, (
            f"{f} imports {sorted(offenders)}. WHY: the gateway must depend on `core` only, "
            "never the worker (SPEC §3). FIX: move shared code into packages/core and import it there."
        )


def test_worker_never_imports_gateway() -> None:
    for f in _py_files(_WORKER_SRC):
        offenders = {m for m in _modules(f) if m.split(".")[0] == "gateway"}
        assert not offenders, (
            f"{f} imports {sorted(offenders)}. WHY: the worker must depend on `core` only, "
            "never the gateway (SPEC §3). FIX: move shared code into packages/core."
        )


def test_domain_is_pure_no_io() -> None:
    domain = _CORE_SRC / "core" / "domain"
    for f in _py_files(domain):
        mods = _modules(f)
        io_offenders = {m for m in mods if m.split(".")[0] in _IO_LIBS}
        infra_offenders = {m for m in mods if m.startswith("core.infra")}
        assert not (io_offenders or infra_offenders), (
            f"{f} imports {sorted(io_offenders | infra_offenders)}. WHY: core/domain must be PURE "
            "logic, unit-testable without Docker (SPEC §3/§5). FIX: put all I/O in core/infra "
            "adapters and keep the domain dependency-free."
        )


def test_no_banned_orchestrator_anywhere() -> None:
    for root in (_CORE_SRC, _GATEWAY_SRC, _WORKER_SRC):
        for f in _py_files(root):
            offenders = {m for m in _modules(f) if m.split(".")[0] in _BANNED_ORCHESTRATORS}
            assert not offenders, (
                f"{f} imports banned orchestrator {sorted(offenders)}. WHY: the spec forbids managed "
                "orchestrators — choreograph with raw aio-pika (SPEC §1). FIX: remove it and use the "
                "broker topology in core/infra/broker.py."
            )
