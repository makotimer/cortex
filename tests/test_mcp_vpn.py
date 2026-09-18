"""service.mcp_vpn must import on mcp 2.x and register exactly its two tools."""

from __future__ import annotations

import asyncio

from mcp.server.mcpserver import MCPServer

from service.mcp_vpn import mcp

EXPECTED_TOOLS = {"vpn_status", "vpn_fetch"}


def test_mcp_vpn_is_mcpserver() -> None:
    assert isinstance(mcp, MCPServer)
    assert mcp.name == "vpn"


def test_mcp_vpn_registers_tools() -> None:
    tools = asyncio.run(mcp.list_tools())
    assert {t.name for t in tools} == EXPECTED_TOOLS


def test_vpn_fetch_defaults_keep_the_exit() -> None:
    tools = {t.name: t for t in asyncio.run(mcp.list_tools())}
    props = tools["vpn_fetch"].input_schema["properties"]
    assert props["rotate"]["default"] is False
    assert props["as_text"]["default"] is True
