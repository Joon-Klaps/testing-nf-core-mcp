"""The rules the server enforces, whatever client or model is connected.

Every check raises Refused with a sentence the model can read and act on. M2 and M3 add their checks here (validation ids, profile allow list, allowed paths).
"""

from mcp.server.mcpserver.exceptions import ToolError

from nfcore_mcp.index import IndexEntry


class Refused(ToolError):
    """A request the server will not carry out. The SDK returns the message to the client as a tool error."""


def check_pipeline(name: str, entries: list[IndexEntry]) -> IndexEntry:
    """Return the index entry for name, or refuse: only indexed nf-core pipelines, never an arbitrary repository."""
    for entry in entries:
        if entry["name"] == name:
            return entry
    raise Refused(f"nf-core/{name} is not in the index. Use search_pipelines to find a pipeline.")


def check_release(entry: IndexEntry, release: str) -> None:
    """Refuse `dev` and any release other than the indexed one. Pinned releases only."""
    if release != entry["release"]:
        raise Refused(
            f'nf-core/{entry["name"]} is only available at release {entry["release"]}, not {release!r}. Retry with release="{entry["release"]}".'
        )
