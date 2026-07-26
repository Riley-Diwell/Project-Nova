"""
Client that talks to the MCP server. To use this:

    - list_tools() to find available tools
    - call_tools() to run a tool

Used by mcp_bootstrap.py and mcp_adapter.py.
    
If the server changes we only need to update this file.
"""

from __future__ import annotations
from typing import Any
from fastmcp import Client
from .mcp_server import mcp


class MCPClient:
    """
    Handles communication with the MCP server.
    """

    def __init__(self, server: Any = mcp) -> None:
        self._server = server

    async def list_tools(self) -> list[dict[str, Any]]:
        """
        Get a list of tools {name, description, input_schema}
        available on the MCP server.
        """
        async with Client(self._server) as client:

            # Ask MCP server for it's available tools.
            tools = await client.list_tools()

            return [
                {
                    "name": t.name,
                    "description": t.description or "",
                    "input_schema": t.inputSchema,
                }
                for t in tools
            ]

    async def call_tool(self, name: str, tool_input: dict[str, Any]) -> Any:
        """
        Runs one tool on the MCP server and returns the result.
        """
        async with Client(self._server) as client:
            result = await client.call_tool(name, tool_input)
            return result.data