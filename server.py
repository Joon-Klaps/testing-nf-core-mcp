from typing import List
from mcp.server import MCPServer

mcp = MCPServer("nf-core-mcp", "0.1.0")

@mcp.tool()
def hello(name: str) -> List[str]:
    """
    Return the name of the person and say hello to them.
    """
    return [f"Hello, {name}!"]

if __name__ == "__main__":
    mcp.run(transport='stdio')