"""Probe registry. Each probe is fn(ctx) -> Finding | list[Finding].

Probes must be read-only, must only execute commands through ctx.runner
(allowlisted), and must report UNKNOWN with a reason instead of guessing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..model import Finding


@dataclass(frozen=True)
class Probe:
    id: str
    category: str
    title: str
    func: Callable


REGISTRY: list[Probe] = []


def probe(id: str, category: str, title: str):
    def wrap(func):
        if any(p.id == id for p in REGISTRY):
            raise ValueError(f"duplicate probe {id}")
        REGISTRY.append(Probe(id, category, title, func))
        return func
    return wrap


def finding(probe_id: str, category: str, title: str, severity: str, summary: str, **kw) -> Finding:
    return Finding(id=probe_id, category=category, title=title, severity=severity,
                   summary=summary, **kw)


def load_all() -> list[Probe]:
    # Import order defines default display order within categories.
    from . import system, updates, security, hardware, storage, software, network, logs  # noqa: F401
    return list(REGISTRY)
