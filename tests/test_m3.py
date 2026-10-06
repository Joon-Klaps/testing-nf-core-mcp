"""Offline tests for M3. Run with `uv run pytest tests/test_m3.py`.

tests/fixtures/ holds console output and a trace file from a real Nextflow 26.04.6 run with one failing task (paths shortened), and the methods-description section of a real nf-core/viralmetagenome MultiQC report. Launching is tested with a fake `nextflow` script, so no Docker and no network are needed.
"""

import asyncio
import json
import subprocess
import time
from pathlib import Path

import pytest
import yaml
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from nfcore_mcp import audit, runs
from nfcore_mcp.guardrails import Refused, check_profiles, check_run_id
from nfcore_mcp.runs import RUN_ID, RunRecord
from nfcore_mcp.summary import (
    build_summary,
    find_multiqc,
    general_stats,
    methods_description,
    output_folders,
    software_versions,
    unique_references,
)

FIXTURES = Path(__file__).parent / "fixtures"

# Stands in for Nextflow: checks it was started in a run folder, writes a trace row and console output, and exits with $FAKE_EXIT.
FAKE_NEXTFLOW = """#!/bin/sh
test -f params.yaml || { echo "no params.yaml in $(pwd)"; exit 9; }
printf 'task_id\\thash\\tname\\tstatus\\texit\\trealtime\\tworkdir\\n1\\tab/cdef12\\tFASTQC (A)\\tCOMPLETED\\t0\\t1s\\t/w\\n' > trace.txt
echo "[PIPELINE] nf-core/demo 1.2.0 | profile=test,docker"
if [ "${FAKE_EXIT:-0}" != "0" ]; then echo "[ERROR] ERROR ~ Error executing process > 'MULTIQC'"; fi
exit "${FAKE_EXIT:-0}"
"""


def record(**overrides) -> RunRecord:
    base = RunRecord(
        run_id="demo-20261006-120000-abcd",
        name="demo",
        release="1.2.0",
        profiles=["test", "docker"],
        params={"input": "/data/s.csv", "outdir": "/runs/x/results"},
        pid=0,
        started_at="2026-10-06T12:00:00",
        command=[],
    )
    return RunRecord(**{**base, **overrides})


@pytest.fixture
def fake_nextflow(tmp_path) -> str:
    path = tmp_path / "fake_nextflow"
    path.write_text(FAKE_NEXTFLOW)
    path.chmod(0o755)
    return str(path)


def wait_for_exit(run_dir: Path, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while not (run_dir / "exitcode").exists():
        assert time.monotonic() < deadline, "the run did not finish in time"
        time.sleep(0.05)


# --- runs.py ---


def test_new_run_id_matches_the_pattern():
    assert RUN_ID.match(runs.new_run_id("viralmetagenome"))


def test_nextflow_command_pins_release_and_uses_run_folder_files():
    command = runs.nextflow_command("demo", "1.2.0", ["test", "docker"], nextflow="nextflow")
    assert command[:3] == ["nextflow", "run", "nf-core/demo"]
    joined = " ".join(command)
    for part in ["-r 1.2.0", "-profile test,docker", "-params-file params.yaml", "-c trace.config", "-ansi-log false"]:
        assert part in joined


def test_start_writes_the_run_folder_and_completes(tmp_path, fake_nextflow):
    run_dir = tmp_path / "runs" / "demo-20261006-120000-abcd"
    rec = runs.start(run_dir, "demo", "1.2.0", ["test"], {"input": "/data/s.csv"}, nextflow=fake_nextflow)

    params = yaml.safe_load((run_dir / "params.yaml").read_text())
    assert params == {"input": "/data/s.csv", "outdir": str(run_dir / "results")}
    assert "trace" in (run_dir / "trace.config").read_text()
    assert runs.load_record(run_dir) == rec

    wait_for_exit(run_dir)
    status = runs.status(run_dir)
    assert status["state"] == "completed"
    assert status["tasks"] == {"COMPLETED": 1}
    assert status["error"] == []


def test_start_reports_a_failed_run_with_its_error(tmp_path, fake_nextflow, monkeypatch):
    monkeypatch.setenv("FAKE_EXIT", "1")
    run_dir = tmp_path / "demo-20261006-120000-abcd"
    runs.start(run_dir, "demo", "1.2.0", ["test"], {}, nextflow=fake_nextflow)
    wait_for_exit(run_dir)
    status = runs.status(run_dir)
    assert status["state"] == "failed"
    assert "Error executing process > 'MULTIQC'" in status["error"][0]


def test_status_is_stopped_when_the_process_is_gone_without_an_exit_code(tmp_path):
    finished = subprocess.Popen(["true"])
    finished.wait()
    (tmp_path / "run.json").write_text(json.dumps(record(pid=finished.pid)))
    assert runs.status(tmp_path)["state"] == "stopped"


def test_read_trace_on_real_nextflow_output():
    rows = runs.read_trace(FIXTURES / "trace_failed.txt")
    assert [r["status"] for r in rows] == ["COMPLETED", "COMPLETED", "FAILED"]
    assert rows[2]["name"] == "FAIL (1)"
    assert rows[2]["workdir"].startswith("/runs/demo-run/work/")


def test_read_trace_before_any_task_finished(tmp_path):
    assert runs.read_trace(tmp_path / "trace.txt") == []


def test_error_block_on_real_nextflow_output():
    block = runs.error_block((FIXTURES / "nextflow_failed.out").read_text())
    assert "Error executing process > 'FAIL (1)'" in block[0]
    assert any("boom on stderr" in line for line in block)
    assert len(block) <= runs.MAX_ERROR_LINES


def test_error_block_falls_back_to_the_last_lines():
    console = "\n".join(f"line {i}" for i in range(100)) + "\nKilled"
    block = runs.error_block(console)
    assert block[-1] == "Killed"
    assert len(block) == runs.MAX_ERROR_LINES


# --- guardrails.py ---


def test_check_profiles_splits_and_allows_known_profiles():
    assert check_profiles("test, docker,emulate_amd64", {"test", "docker", "emulate_amd64"}) == [
        "test",
        "docker",
        "emulate_amd64",
    ]


@pytest.mark.parametrize("profile", ["test,vsc_kul_uhasselt", "", " , "])
def test_check_profiles_refuses_unknown_or_empty(profile):
    with pytest.raises(Refused):
        check_profiles(profile, {"test", "docker"})


def test_check_run_id_returns_an_existing_run(tmp_path):
    run_dir = tmp_path / "demo-20261006-120000-abcd"
    run_dir.mkdir()
    (run_dir / "run.json").write_text("{}")
    assert check_run_id("demo-20261006-120000-abcd", tmp_path) == run_dir


@pytest.mark.parametrize("run_id", ["../../etc", "/etc/passwd", "demo-20261006-120000-ffff", "samplesheets"])
def test_check_run_id_refuses_bad_or_unknown_ids(tmp_path, run_id):
    (tmp_path / "samplesheets").mkdir()
    with pytest.raises(Refused):
        check_run_id(run_id, tmp_path)


# --- summary.py ---


def multiqc_folder(results: Path, report_html: str = "<html></html>") -> Path:
    data = results / "multiqc" / "multiqc_data"
    data.mkdir(parents=True)
    (results / "multiqc" / "multiqc_report.html").write_text(report_html, encoding="utf-8")
    return results / "multiqc"


def test_output_folders_counts_files_per_top_level_folder(tmp_path):
    (tmp_path / "fastqc" / "A").mkdir(parents=True)
    (tmp_path / "fastqc" / "A" / "a.html").write_text("")
    (tmp_path / "fastqc" / "b.zip").write_text("")
    (tmp_path / "multiqc").mkdir()
    (tmp_path / "loose_file.txt").write_text("")
    assert output_folders(tmp_path) == {"fastqc": 2, "multiqc": 0}


def test_find_multiqc_searches_below_results(tmp_path):
    assert find_multiqc(tmp_path) is None
    assert find_multiqc(tmp_path / "missing") is None
    multiqc = multiqc_folder(tmp_path)
    assert find_multiqc(tmp_path) == multiqc


def test_general_stats_reads_the_multiqc_table(tmp_path):
    multiqc = multiqc_folder(tmp_path)
    (multiqc / "multiqc_data" / "multiqc_general_stats.txt").write_text("Sample\tpercent_gc\nA\t41.0\nB\t43.5\n")
    assert general_stats(multiqc) == [{"Sample": "A", "percent_gc": "41.0"}, {"Sample": "B", "percent_gc": "43.5"}]


def test_software_versions_flattens_and_keeps_every_version(tmp_path):
    multiqc = multiqc_folder(tmp_path)
    (multiqc / "multiqc_data" / "multiqc_software_versions.txt").write_text(
        "Sample\tsamtools\tfastqc\tNextflow\n"
        "SAMTOOLS_SORT\t1.22\t\t\n"
        "SAMTOOLS_INDEX\t1.21\t\t\n"
        "FASTQC\t\t0.12.1\t\n"
        "Workflow\t\t\t26.04.6\n"
    )
    assert software_versions(multiqc) == {"fastqc": "0.12.1", "Nextflow": "26.04.6", "samtools": "1.21, 1.22"}


def test_methods_description_from_a_real_report():
    html = (FIXTURES / "multiqc_methods_viralmetagenome.html").read_text(encoding="utf-8")
    description = methods_description(html)
    assert description is not None

    methods = description["methods"]
    assert methods.startswith("Data was processed using nf-core/viralmetagenome")
    assert "Nextflow v26.04.1" in methods
    assert "nextflow run . -profile docker,test" in methods  # the command, from <pre><code>
    assert "Ewels et al., 2020" in methods  # <em>et al.</em> flattened to text
    assert "<" not in methods and "&amp;" not in methods

    references = description["references"]
    assert any("Improved Metagenomic Analysis with Kraken 2" in r for r in references)
    assert sum("Nextflow enables reproducible computational workflows" in r for r in references) == 1  # listed twice in the report
    assert not any("command above does not include" in r for r in references)  # the Notes list after the references


def test_methods_description_none_without_the_section():
    assert methods_description("<html><h4>Methods</h4><p>Something else</p></html>") is None


def test_unique_references_by_doi_case_insensitive():
    refs = ["A. doi: 10.1038/nbt.3820", "B, doi:10.1038/NBT.3820.", "C (no doi)", "D (no doi)"]
    assert unique_references(refs) == ["A. doi: 10.1038/nbt.3820", "C (no doi)", "D (no doi)"]


def test_build_summary_without_multiqc_hides_server_params(tmp_path):
    (tmp_path / "results" / "fastqc").mkdir(parents=True)
    rec = record(params={"input": "/data/s.csv", "outdir": "/runs/x", "skip_trim": True})
    result = build_summary(tmp_path, rec)
    assert result["multiqc_report"] is None and result["methods"] is None and result["references"] == []
    assert result["outputs"] == {"fastqc": 0}
    assert result["parameters"] == {"skip_trim": True}


def test_build_summary_with_a_real_methods_section(tmp_path):
    html = (FIXTURES / "multiqc_methods_viralmetagenome.html").read_text(encoding="utf-8")
    multiqc_folder(tmp_path / "results", html)
    result = build_summary(tmp_path, record())
    assert result["multiqc_report"] == str(tmp_path / "results" / "multiqc" / "multiqc_report.html")
    assert result["methods"] and result["methods"].startswith("Data was processed")


# --- audit.py ---


@pytest.fixture
def audit_log(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "audit.log"
    monkeypatch.setattr(audit, "LOG_PATH", path)
    return path


def entries(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_audited_logs_ok_refused_and_error(audit_log):
    @audit.audited
    def tool(name: str, k: int = 5) -> dict:
        if name == "refuse":
            raise Refused("no")
        if name == "fail":
            raise ToolError("GitHub down")
        if name == "bug":
            raise KeyError("oops")
        return {"run_id": "demo-20261006-120000-abcd"}

    tool("ok")
    for name, exc in [("refuse", Refused), ("fail", ToolError), ("bug", KeyError)]:
        with pytest.raises(exc):
            tool(name)

    logged = entries(audit_log)
    assert [e["outcome"] for e in logged] == ["ok", "refused", "error", "error"]
    assert logged[0]["arguments"] == {"name": "ok", "k": 5}
    assert logged[0]["run_id"] == "demo-20261006-120000-abcd"
    assert logged[2]["detail"] == "GitHub down"
    assert logged[3]["detail"].startswith("KeyError")


def test_audited_async_tool(audit_log):
    @audit.audited
    async def tool(name: str) -> str:
        return name

    assert asyncio.run(tool("x")) == "x"
    assert entries(audit_log)[0]["tool"] == "tool"


def test_audited_tool_keeps_its_mcp_schema(audit_log):
    server = MCPServer("test")

    @server.tool()
    @audit.audited
    async def find(problem: str, k: int = 5) -> list[str]:
        """Find things."""
        return [problem] * k

    (tool,) = asyncio.run(server.list_tools())
    assert tool.name == "find"
    assert tool.description == "Find things."
    assert set(tool.input_schema["properties"]) == {"problem", "k"}
    assert tool.input_schema["required"] == ["problem"]

    asyncio.run(server.call_tool("find", {"problem": "viral", "k": 2}))
    assert entries(audit_log)[0]["arguments"] == {"problem": "viral", "k": 2}


def test_error_block_starts_at_the_parser_diagnostic():
    # Real output of nf-core/viralrecon 3.0.0 under Nextflow 26's strict parser: the cause comes before "ERROR ~".
    block = runs.error_block((FIXTURES / "nextflow_config_error.out").read_text())
    assert block[0].startswith("Error nextflow.config:252:26: Invalid include source")
    assert any("Config parsing failed" in line for line in block)


def test_nextflow_env_sets_the_syntax_parser(monkeypatch):
    monkeypatch.setattr(runs, "NEXTFLOW_SYNTAX_PARSER", "v1")
    assert runs.nextflow_env()["NXF_SYNTAX_PARSER"] == "v1"
