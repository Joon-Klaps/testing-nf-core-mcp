"""Start a pipeline run in the background and read its state back from disk.

Every run gets its own folder, runs/<run_id>/, and Nextflow runs with that folder as its working directory, so `.nextflow/`, `.nextflow.log` and `work/` of one run never mix with another's:

    run.json        what was launched: pipeline, release, profiles, params, pid (written by start)
    params.yaml     the validated parameters, with outdir pointing at results/
    trace.config    asks Nextflow for a trace file with the fields run_status needs
    nextflow.out    Nextflow's console output, which holds the error block when a run fails
    trace.txt       one row per finished task, written by Nextflow while it runs
    exitcode        Nextflow's exit status, written by the wrapper shell once Nextflow ends
    results/        the pipeline's outputs

All state lives in these files, not in the server's memory. A restarted server, or a second client, can still follow a run.
"""

import json
import os
import re
import secrets
import shutil
import subprocess
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Literal, TypedDict

import yaml

from nfcore_mcp.config import EXTRA_PATH, NEXTFLOW

# "demo-20261006-142501-a3f9": pipeline, start time, 4 random hex characters so two launches in one second differ.
RUN_ID = re.compile(r"^[a-z0-9]+-\d{8}-\d{6}-[0-9a-f]{4}$")

# Nextflow's default trace has no workdir; run_status uses it to point at a failed task's folder. -c config overrides the pipeline's own trace block.
TRACE_CONFIG = """\
trace {
    enabled = true
    overwrite = true
    file = 'trace.txt'
    fields = 'task_id,hash,name,status,exit,realtime,workdir'
}
"""

# The longest error block run_status returns. Nextflow's block holds the command, its output and its stderr; 40 lines covers it without flooding the model.
MAX_ERROR_LINES = 40

State = Literal["running", "completed", "failed", "stopped"]


class RunRecord(TypedDict):
    run_id: str
    name: str
    release: str
    profiles: list[str]
    params: dict
    pid: int
    started_at: str
    command: list[str]


class RunStatus(TypedDict):
    run_id: str
    state: State  # stopped: no exit code and no process, e.g. the machine slept or the process was killed
    tasks: dict[str, int]  # trace status -> count, e.g. {"COMPLETED": 7, "FAILED": 1}
    failed_tasks: list[str]
    error: list[str]  # Nextflow's error block, only when failed or stopped


def new_run_id(name: str) -> str:
    """A fresh run id that matches RUN_ID."""
    return f"{name}-{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"


def nextflow_env() -> dict[str, str]:
    """The environment for the Nextflow process: the server's own, with EXTRA_PATH appended to PATH."""
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([env.get("PATH", ""), *EXTRA_PATH])
    return env


def nextflow_command(name: str, release: str, profiles: list[str], nextflow: str = NEXTFLOW) -> list[str]:
    """The Nextflow command line, with every path relative to the run folder it is started in.

    -ansi-log false: plain console lines that can be parsed, instead of a live-updating progress display.
    """
    return [
        nextflow,
        "run",
        f"nf-core/{name}",
        "-r",
        release,
        "-profile",
        ",".join(profiles),
        "-params-file",
        "params.yaml",
        "-c",
        "trace.config",
        "-ansi-log",
        "false",
    ]


def docker_available() -> bool:
    """True when the docker CLI is found and its daemon answers. With Colima, the daemon is down until `colima start`."""
    docker = shutil.which("docker", path=nextflow_env()["PATH"])
    if docker is None:
        return False
    result = subprocess.run([docker, "info"], capture_output=True, env=nextflow_env(), timeout=20)
    return result.returncode == 0


def start(
    run_dir: Path, name: str, release: str, profiles: list[str], params: dict, nextflow: str = NEXTFLOW
) -> RunRecord:
    """Create run_dir, write its input files, and start Nextflow in the background. Return at once with the run record.

    Nextflow runs under `sh -c '"$@"; echo $? > exitcode'`: the shell waits for Nextflow and writes its exit status, so the outcome is on disk even when the server has gone. start_new_session=True puts the run in its own process group, so it is not killed when the client stops the server.
    """
    run_dir.mkdir(parents=True)
    params = {**params, "outdir": str(run_dir / "results")}
    (run_dir / "params.yaml").write_text(yaml.safe_dump(params, sort_keys=False))
    (run_dir / "trace.config").write_text(TRACE_CONFIG)

    command = nextflow_command(name, release, profiles, nextflow)
    with open(run_dir / "nextflow.out", "w") as console:
        process = subprocess.Popen(
            ["sh", "-c", '"$@"; echo $? > exitcode', "sh", *command],
            cwd=run_dir,
            stdin=subprocess.DEVNULL,
            stdout=console,
            stderr=subprocess.STDOUT,
            env=nextflow_env(),
            start_new_session=True,
        )

    record = RunRecord(
        run_id=run_dir.name,
        name=name,
        release=release,
        profiles=profiles,
        params=params,
        pid=process.pid,
        started_at=datetime.now().isoformat(timespec="seconds"),
        command=command,
    )
    (run_dir / "run.json").write_text(json.dumps(record, indent=2))
    return record


def load_record(run_dir: Path) -> RunRecord:
    """The run record start() wrote."""
    return RunRecord(**json.loads((run_dir / "run.json").read_text()))


def process_alive(pid: int) -> bool:
    """True when a process with this pid exists. Signal 0 checks without sending anything."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, but belongs to another user
    return True


def read_trace(path: Path) -> list[dict[str, str]]:
    """The rows of a Nextflow trace file (tab-separated, with a header), or [] before the first task has finished."""
    if not path.exists():
        return []
    lines = path.read_text().splitlines()
    if not lines:
        return []
    header = lines[0].split("\t")
    return [dict(zip(header, line.split("\t"))) for line in lines[1:] if line]


def error_block(console: str) -> list[str]:
    """Nextflow's error report from its console output: from the first "ERROR ~" line, at most MAX_ERROR_LINES lines.

    Nextflow 26 with -ansi-log false prefixes it ("[ERROR] ERROR ~ Error executing process > 'FAIL (1)'"), so look for the marker anywhere in the line.
    Without an "ERROR ~" line (killed, out of memory, Java missing), the last lines are the best clue, so return those.
    """
    lines = console.splitlines()
    for i, line in enumerate(lines):
        if "ERROR ~" in line:
            return lines[i : i + MAX_ERROR_LINES]
    return lines[-MAX_ERROR_LINES:]


def status(run_dir: Path) -> RunStatus:
    """The state of a run, read from its folder."""
    record = load_record(run_dir)
    exitcode_file = run_dir / "exitcode"

    state: State
    if exitcode_file.exists():
        state = "completed" if exitcode_file.read_text().strip() == "0" else "failed"
    elif process_alive(record["pid"]):
        state = "running"
    else:
        state = "stopped"

    trace = read_trace(run_dir / "trace.txt")
    error: list[str] = []
    if state in ("failed", "stopped"):
        console = run_dir / "nextflow.out"
        error = error_block(console.read_text() if console.exists() else "")

    return RunStatus(
        run_id=record["run_id"],
        state=state,
        tasks=dict(Counter(row.get("status", "") for row in trace)),
        failed_tasks=[row.get("name", "") for row in trace if row.get("status") == "FAILED"],
        error=error,
    )
