"""

Jay you commented in SyRS that youre making the server - is that still the case?
"""

from fastmcp import FastMCP

# one shared MCP server
mcp = FastMCP("nova")



# import runs the module's @mcp.tool decorators, which will register the tool onto the MCP server
from app.tools.example import demo_tool_mcp  # (placeholder - remove once a real tool is registered)

# from app.functions.function1 import tools as function1_tool
# from app.functions.function2 import tools as function1_tool
# from app.functions.function3 import tools as function1_tool


if __name__ == "__main__":
    mcp.run()
