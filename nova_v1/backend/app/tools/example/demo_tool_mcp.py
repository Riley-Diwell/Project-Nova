"""
Demo tool since no Function tools exist yet.
"""

from app.tools.mcp_server import mcp


@mcp.tool
def demo_echo(message: str) -> str:
    """
    Echoes whatever input it's given. 
    Demo tool only - not a real function.
    """
    return message
