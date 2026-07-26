""" 
Starts up the MCP tool system. Connects:

    - MCP server
    - MCP client
    - MCPTool
    - ToolRegistry
    - GainStore

Upon startup we discover all MCP tools and register them so the rest of the 
backend can use them.
"""

from __future__ import annotations

import asyncio

from ..gain.gain_store import GainStore
from .dispatcher import Dispatcher
from .mcp_adapter import MCPTool
from .mcp_client import MCPClient
from .registry import ToolRegistry


async def bootstrap_tools(
    client: MCPClient | None = None,
    gain_store: GainStore | None = None,
) -> ToolRegistry:
    """
    Discover every tool on the MCP server and register it, which stores
    available tools with their schemas and gain values.
    """

    # create the MCP client
    client = client or MCPClient()

    # create a registry to save tool gains
    registry = ToolRegistry(gain_store=gain_store or GainStore())

    # discover tools on MCP server
    discovered = await client.list_tools()

    # register each discovered tool
    for entry in discovered:
        registry.register(MCPTool.from_discovered(entry, client))

    return registry


def bootstrap_tools_sync(**kwargs) -> ToolRegistry:
    """
    Synchronous version of bootstrap_tools().
    """
    return asyncio.run(bootstrap_tools(**kwargs))


def build_dispatcher(registry: ToolRegistry) -> Dispatcher:
    """
    Create a dispatcher using registered tools.
    """
    return Dispatcher(registry)