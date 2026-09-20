"""Pluggable extraction domains.

The loop itself -- validate, cluster, repair, verify, train, gate -- is
domain-agnostic. What changes between use cases is only:

  * the schema        (what fields exist and what makes them valid)
  * the registries    (the private facts nobody outside your org knows)
  * the enrichment    (which fields code can derive, and how)
  * the prompts       (serving, and the repair model's reference data)
  * the generator     (how synthetic documents with known gold are produced)

A Domain bundles exactly those five things. Everything else is shared.

Select one with DOMAIN=invoice or DOMAIN=support_email.
"""

from __future__ import annotations

import importlib
import os
import threading
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel


@runtime_checkable
class Domain(Protocol):
    """What a use case must provide to plug into the loop."""

    name: str

    #: The pydantic model that defines "valid".
    Schema: type[BaseModel]

    #: Leaf field paths the scorer compares, for per-field accuracy.
    SCORED_FIELDS: list[str]

    def system_prompt(self) -> str:
        """Instructions for the SERVING model. Must NOT contain the registry."""

    def reference_prompt(self) -> str:
        """Private reference data, given ONLY to the repair model."""

    def enrich(self, payload: dict) -> tuple[dict, dict[str, Any]]:
        """Derive every field that has a right answer. Returns (payload, report).

        This is where most of the value is: anything code can compute should
        never be left to the model. Measured on the invoice domain, moving
        this to serve time took valid_rate from 0% to 95%.
        """

    def mechanical(self, payload: dict) -> dict:
        """Deterministic repair: normalise formats, then derive what code can.

        Must be domain-specific, because it drops keys the schema forbids. The
        invoice implementation was called for every domain at one point, which
        stripped support-email payloads down to `{}` before they were verified
        -- so the email track recorded zero repairs and looked like a model
        failure rather than a wiring bug.
        """

    def check_rules(self, payload: dict) -> list[dict]:
        """Business-rule violations, checked independently of pydantic ordering.

        pydantic aborts at the first failing layer, which masks rule errors
        sitting behind a bad field and skews the failure clusters.
        """


_REGISTRY: dict[str, Domain] = {}


def register(domain: Domain) -> Domain:
    _REGISTRY[domain.name] = domain
    return domain


def get_domain(name: str | None = None) -> Domain:
    """Resolve the active domain. Defaults to DOMAIN, then 'invoice'."""
    key = (name or os.getenv("DOMAIN", "invoice")).strip().lower()
    _load_all()
    if key not in _REGISTRY:
        raise ValueError(
            f"unknown domain {key!r}. available: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[key]


def available() -> list[str]:
    _load_all()
    return sorted(_REGISTRY)


_LOADED = False
_LOCK = threading.Lock()

#: Every module that registers a domain. Loaded by name, not by attribute.
_MODULES = ("domains.invoice", "domains.support_email")


def _load_all() -> None:
    """Import every domain module exactly once, safely under threads.

    Both details here are load-bearing, and both were bugs:

    * The guard is a flag, not `if not _REGISTRY`. The scorer resolves the
      domain from inside a ThreadPoolExecutor, so one thread could register
      `invoice` while another was still importing `support_email`; the second
      thread then saw a non-empty registry, skipped loading, and raised
      "unknown domain 'support_email'" -- intermittently, depending on timing.
    * Modules are imported via importlib by name rather than
      `from domains import invoice, support_email`. The `from` form fails with
      "cannot import name ... from partially initialized module" when another
      thread is midway through importing that submodule.
    """
    global _LOADED
    if _LOADED:
        return
    with _LOCK:
        if _LOADED:
            return
        for module in _MODULES:
            # Lazily, so one broken domain cannot take down the whole package.
            importlib.import_module(module)
        _LOADED = True
