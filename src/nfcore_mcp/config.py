"""Where the server keeps its files.

Clients such as Claude Desktop start the server from an arbitrary working directory, so paths are anchored to the repository, not to the current directory. Set NFCORE_MCP_HOME to put them elsewhere.
"""

import os
from pathlib import Path

HOME = Path(os.environ.get("NFCORE_MCP_HOME", Path(__file__).resolve().parents[2]))

SHORTLIST_FILE = HOME / "shortlist.txt"
INDEX_FILE = HOME / "index" / "pipelines.json"

# Schemas are fetched once per pipeline release and kept here. A release tag never changes, so the cache never goes stale.
SCHEMA_CACHE_DIR = HOME / "index" / "schemas"

# Everything the server writes: generated samplesheets now, run folders in M3.
RUNS_DIR = HOME / "runs"
SAMPLESHEET_DIR = RUNS_DIR / "samplesheets"

# The only directories the model may point the server at for reading data. Several directories are separated by ":" (os.pathsep).
ALLOWED_DATA_DIRS = [
    Path(p).expanduser().resolve() for p in os.environ.get("NFCORE_MCP_DATA_DIRS", str(HOME / "data")).split(os.pathsep)
]

# Every tool call, refused ones included, as one JSON object per line.
AUDIT_LOG = RUNS_DIR / "audit.log"

# Profiles a launch may combine. Anything else (an institutional config, a custom -c file) is refused.
ALLOWED_PROFILES = {"test", "docker", "emulate_amd64", "arm64", "singularity", "conda"}

# Clients such as Claude Desktop start the server with a minimal PATH, without ~/bin or Homebrew. Nextflow and the docker CLI it calls must still be found.
EXTRA_PATH = [str(Path.home() / "bin"), "/opt/homebrew/bin", "/usr/local/bin"]
NEXTFLOW = os.environ.get("NFCORE_MCP_NEXTFLOW", "nextflow")
