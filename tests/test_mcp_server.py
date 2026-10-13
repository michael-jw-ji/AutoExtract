"""The MCP server's tools, without a model or the network."""

from __future__ import annotations

import asyncio

import mcp_server
from core.config import settings
from tests.test_form4 import gold, tx


def test_the_server_exposes_both_tools():
    tools = asyncio.run(mcp_server.server.list_tools())
    assert sorted(t.name for t in tools) == ["extract_form4", "get_eval_summary"]


def test_compare_names_the_fields_that_differ():
    g = gold(tx())
    p = gold(tx(shares="99"))
    out = mcp_server._compare(p, g)
    assert out["fields_with_differences"] == ["transactions.*.shares"]
    assert 0 < out["field_f1"] < 1 and out["xml_transactions"] == 1


def test_summary_works_on_a_fresh_clone_without_a_database(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "db_path", tmp_path / "missing.db")
    s = mcp_server.get_eval_summary()
    assert s["incumbent"] and s["field_f1"] and s["gate_decisions"]
    assert not (tmp_path / "missing.db").exists()
