"""Log every tool call, refused and failed ones included, to runs/audit.log.

One log for the whole server rather than one per run: a refused launch never gets a run folder, and it is exactly the call worth having on record. Each line is one JSON object, so the log can be read with `jq` or a few lines of Python.

Usage, under the MCP decorator so the SDK registers the logged version:

    @mcp.tool()
    @audited
    def search_pipelines(...): ...
"""

import functools
import inspect
import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from nfcore_mcp.config import AUDIT_LOG
from nfcore_mcp.guardrails import Refused

# Module-level so tests can point it at a temporary file.
LOG_PATH: Path = AUDIT_LOG


def outcome_of(error: BaseException | None) -> str:
    """ok, refused (a guardrail said no), or error (a ToolError, or a bug)."""
    if error is None:
        return "ok"
    if isinstance(error, Refused):
        return "refused"
    return "error"


def write_entry(tool: str, arguments: dict[str, Any], error: BaseException | None, result: Any = None) -> None:
    """Append one line to the log. A run_id in the result (launch) is copied in, so a run can be traced back to the call that started it."""
    entry: dict[str, Any] = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "tool": tool,
        "arguments": arguments,
        "outcome": outcome_of(error),
    }
    if error is not None:
        entry["detail"] = str(error) if isinstance(error, ToolError) else f"{type(error).__name__}: {error}"
    if isinstance(result, dict) and "run_id" in result:
        entry["run_id"] = result["run_id"]

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def audited(fn: Callable) -> Callable:
    """Wrap a tool so that every call is logged with its arguments and outcome. Exceptions are logged, then raised unchanged.

    functools.wraps copies the name, docstring and signature, which is what the SDK reads to build the tool's input and output schema. Async tools get an async wrapper, so the SDK still awaits them.
    """
    signature = inspect.signature(fn)

    def bound_arguments(args: tuple, kwargs: dict) -> dict[str, Any]:
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        return dict(bound.arguments)

    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs):
            arguments = bound_arguments(args, kwargs)
            try:
                result = await fn(*args, **kwargs)
            except Exception as e:
                write_entry(fn.__name__, arguments, e)
                raise
            write_entry(fn.__name__, arguments, None, result)
            return result

        return async_wrapper

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        arguments = bound_arguments(args, kwargs)
        try:
            result = fn(*args, **kwargs)
        except Exception as e:
            write_entry(fn.__name__, arguments, e)
            raise
        write_entry(fn.__name__, arguments, None, result)
        return result

    return wrapper
