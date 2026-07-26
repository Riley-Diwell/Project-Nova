"""
mcp_adapter.py - Section 5.7: MCP tool adapter (Naoise)

Makes a tool living on the shared MCP server (mcp_server.py) look, to
ToolRegistry and Dispatcher, exactly like a local BaseTool - so nothing
in registry.py or dispatcher.py has to know or care whether a tool runs
in-process or over MCP.

WHAT THIS FILE IS
    MCPTool(BaseTool): built from one entry out of MCPClient.list_tools()
    - {name, description, input_schema} - plus a reference to the client.
    invoke() forwards to the shared server via MCPClient.call_tool()
    instead of running local code.

WHO USES THIS
- mcp_bootstrap.py: wraps every tool discovered on the shared server in
  an MCPTool, then registers each one with ToolRegistry - exactly like
  DemoTool is registered in how_to_use_naoises_code.py.
- After that, Dispatcher/Reinforcer/ToolRegistry use it through the same
  .invoke() surface as any other BaseTool. No special-casing needed.

WHY invoke() USES asyncio.run()
BaseTool.invoke() is sync (see base.py) and the rest of the platform
(Dispatcher, the existing tests) call it synchronously. MCPClient's
methods are async because the underlying MCP call is. asyncio.run() is
the simplest bridge that keeps every other file unchanged. If the
FastAPI layer ends up calling tools from inside an async request
handler later, this can move to a proper `await` path - only the inside
of _execute() would change, the .invoke() contract stays the same.
"""


""" 
MCP version of a Nova tool. MCPTool makes a tool running on an MCP server 
look like a normal BaseTool, meaning Nova can use tool.invoke without knowing 
whether the tool is local or on the MCP server. When MCPTool is invoked it
sends the request to the MCP server and returns the result.  
"""

from __future__ import annotations

import asyncio
from typing import Any

from .base import BaseTool
from .mcp_client import MCPClient


class MCPTool(BaseTool):
    """
    Nova tool that runs through an MCP server. Inherits from BaseTool.
    Sends the request to the MCP server using MCP client.
    """
    def __init__(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        client: MCPClient,
    ) -> None:
        # initialise fields
        super().__init__(name=name, description=description, input_schema=input_schema)

        # store the MCP client so this tool can communicate with the MCP server
        # when its called.
        self._client = client

    def _execute(self, tool_input: dict[str, Any]) -> Any:
        """
        Execute the tool by sending the request to the MCP server.
        """
        return asyncio.run(self._client.call_tool(self.name, tool_input))

    @classmethod
    def from_discovered(cls, discovered: dict[str, Any], client: MCPClient) -> "MCPTool":
        """
        Create an MCP tool from information discovered from the MCP server.
        Used by mcp_bootstrap.py.
        """
        return cls(
            name=discovered["name"],
            description=discovered["description"],
            input_schema=discovered["input_schema"],
            client=client,
        )