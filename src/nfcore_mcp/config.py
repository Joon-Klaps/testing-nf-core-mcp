"""Where the server keeps its files.

Clients such as Claude Desktop start the server from an arbitrary working directory, so paths are anchored to the repository, not to the current directory. Set NFCORE_MCP_HOME to put them elsewhere.
"""

import os
from pathlib import Path

HOME = Path(os.environ.get("NFCORE_MCP_HOME", Path(__file__).resolve().parents[2]))

SHORTLIST_FILE = HOME / "shortlist.txt"
INDEX_FILE = HOME / "index" / "pipelines.json"
