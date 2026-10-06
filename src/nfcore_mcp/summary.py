"""Summarise a finished run: what came out, the headline QC numbers, software versions, and the pipeline's own methods text with references.

Everything comes from the run folder, mostly from MultiQC's output. Every nf-core pipeline ends with MultiQC, and its report carries a methods description written by the pipeline itself: pipeline version and DOI, Nextflow version, the exact command, the tools used, and a reference list. Using that text, rather than writing one here, means the summary says what the pipeline says about itself. No HTTP and no MCP in here.

    results/multiqc/multiqc_report.html                      methods description and references (only in the HTML)
    results/multiqc/multiqc_data/multiqc_general_stats.txt   one row per sample, the report's headline table
    results/multiqc/multiqc_data/multiqc_software_versions.txt  one row per process, one column per tool
"""

import csv
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import TypedDict

from nfcore_mcp.runs import RunRecord

REPORT = "multiqc_report.html"
GENERAL_STATS = "multiqc_general_stats.txt"
SOFTWARE_VERSIONS = "multiqc_software_versions.txt"
MAX_STATS_ROWS = 20

# The server sets these itself; they are local paths, not choices worth reporting.
SERVER_PARAMS = {"input", "outdir"}


class MethodsDescription(TypedDict):
    methods: str  # paragraphs separated by a blank line
    references: list[str]


class RunSummary(TypedDict):
    run_id: str
    pipeline: str
    release: str
    results_dir: str
    multiqc_report: str | None
    outputs: dict[str, int]  # top-level results folder -> number of files in it
    general_stats: list[dict[str, str]]  # at most MAX_STATS_ROWS samples
    software_versions: dict[str, str]
    parameters: dict  # set for this run; the methods text's command line does not show them, because they went in through -params-file
    methods: str | None
    references: list[str]


def output_folders(results_dir: Path) -> dict[str, int]:
    """Each top-level folder in results_dir with the number of files below it, so the model can say where to look."""
    if not results_dir.is_dir():
        return {}
    return {
        folder.name: sum(1 for f in folder.rglob("*") if f.is_file())
        for folder in sorted(results_dir.iterdir())
        if folder.is_dir()
    }


def find_multiqc(results_dir: Path) -> Path | None:
    """The folder holding multiqc_report.html. Usually results/multiqc/, but pipelines may nest it, so search."""
    found = sorted(results_dir.rglob(REPORT)) if results_dir.is_dir() else []
    return found[0].parent if found else None


def general_stats(multiqc_dir: Path) -> list[dict[str, str]]:
    """MultiQC's general statistics table, one dict per sample, or [] when it is missing."""
    path = multiqc_dir / "multiqc_data" / GENERAL_STATS
    if not path.exists():
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))[:MAX_STATS_ROWS]


def software_versions(multiqc_dir: Path) -> dict[str, str]:
    """Tool -> version from MultiQC's software versions table (rows are processes, columns are tools, most cells empty).

    One tool can appear in several processes at different versions (samtools 1.21 in one module, 1.22 in another); those are joined, "1.21, 1.22", rather than one silently winning.
    """
    path = multiqc_dir / "multiqc_data" / SOFTWARE_VERSIONS
    if not path.exists():
        return {}
    versions: dict[str, set[str]] = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            for tool, version in row.items():
                if tool != "Sample" and version:
                    versions.setdefault(tool, set()).add(version)
    return {tool: ", ".join(sorted(found)) for tool, found in sorted(versions.items(), key=lambda item: item[0].lower())}


class _BlockText(HTMLParser):
    """Collects the text of an HTML fragment as blocks: one per paragraph, list item, heading or code block. Tags are dropped, entities decoded, whitespace collapsed."""

    BLOCK_TAGS = {"p", "pre", "li", "ul", "h4", "h5", "div"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self._current: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self.BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        if tag in self.BLOCK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        self._current.append(data)

    def close(self) -> None:
        super().close()
        self._flush()

    def _flush(self) -> None:
        text = " ".join("".join(self._current).split())
        if text:
            self.blocks.append(text)
        self._current = []


def html_blocks(fragment: str) -> list[str]:
    """The text blocks of an HTML fragment, in order."""
    parser = _BlockText()
    parser.feed(fragment)
    parser.close()
    return parser.blocks


DOI = re.compile(r"\b10\.\d{4,9}/[^\s,;]+[^\s,;.]", re.IGNORECASE)


def unique_references(references: list[str]) -> list[str]:
    """Drop a reference whose DOI appeared earlier. The template lists Nextflow and nf-core once in its own format and again in the tool bibliography; the wording differs, the DOI does not. References without a DOI are all kept."""
    seen: set[str] = set()
    unique = []
    for reference in references:
        m = DOI.search(reference)
        doi = m[0].lower() if m else None
        if doi in seen:
            continue
        if doi:
            seen.add(doi)
        unique.append(reference)
    return unique


def methods_description(report_html: str) -> MethodsDescription | None:
    """The methods text and reference list from an nf-core MultiQC report, or None when the report has no methods description.

    The section comes from the nf-core template (assets/methods_description_template.yml), so its shape is the same in every pipeline: inside the element whose id ends in "-methods-description", a "<h4>Methods</h4>" with paragraphs and the command, then "<h4>References</h4>" and a <ul>. Only that <ul> is read; the "Notes" list after it is advice to the reader, not a reference.
    """
    section = re.search(r'id="[^"]*-methods-description"', report_html)
    if not section:
        return None
    methods_start = report_html.find("<h4>Methods</h4>", section.end())
    references_start = report_html.find("<h4>References</h4>", methods_start)
    if methods_start == -1 or references_start == -1:
        return None
    references_end = report_html.find("</ul>", references_start)

    methods_html = report_html[methods_start + len("<h4>Methods</h4>") : references_start]
    references_html = report_html[references_start + len("<h4>References</h4>") : references_end]
    return MethodsDescription(
        methods="\n\n".join(html_blocks(methods_html)),
        references=unique_references(html_blocks(references_html)),
    )


def build_summary(run_dir: Path, record: RunRecord) -> RunSummary:
    """Everything run_summary returns, for a run that has completed. Fields stay empty when the pipeline produced no MultiQC report."""
    results_dir = run_dir / "results"
    multiqc_dir = find_multiqc(results_dir)

    description = None
    if multiqc_dir is not None:
        description = methods_description((multiqc_dir / REPORT).read_text(encoding="utf-8", errors="replace"))

    return RunSummary(
        run_id=record["run_id"],
        pipeline=record["name"],
        release=record["release"],
        results_dir=str(results_dir),
        multiqc_report=str(multiqc_dir / REPORT) if multiqc_dir else None,
        outputs=output_folders(results_dir),
        general_stats=general_stats(multiqc_dir) if multiqc_dir else [],
        software_versions=software_versions(multiqc_dir) if multiqc_dir else {},
        parameters={k: v for k, v in record["params"].items() if k not in SERVER_PARAMS},
        methods=description["methods"] if description else None,
        references=description["references"] if description else [],
    )
