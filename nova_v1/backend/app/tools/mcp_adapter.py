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