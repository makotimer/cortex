"""service.mcp_server must import on mcp 2.x (MCPServer, not FastMCP)."""

from __future__ import annotations

import asyncio

from mcp.server.mcpserver import MCPServer

from service.mcp_server import mcp

EXPECTED_TOOLS = {
    "list_folders",
    "list_emails",
    "read_email",
    "search_emails",
    "move_email",
    "move_emails",
    "send_email",
}


def test_mcp_server_is_mcpserver() -> None:
    assert isinstance(mcp, MCPServer)
    assert mcp.name == "protonmail-bridge"


def test_mcp_server_registers_tools() -> None:
    tools = asyncio.run(mcp.list_tools())
    assert {t.name for t in tools} == EXPECTED_TOOLS
