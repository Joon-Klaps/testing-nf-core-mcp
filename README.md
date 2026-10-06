# nf-core MCP server

A local [Model Context Protocol](https://modelcontextprotocol.io) server that lets Claude take a researcher from a data problem to a finished nf-core pipeline run: find the right pipeline, build and check its samplesheet, launch it on your machine, follow it, and summarise the result with a methods text and references.

```text
"I have paired-end Illumina reads from a viral outbreak and want consensus genomes"
  → search_pipelines        viralrecon, viralmetagenome, with the README lines that matched
  → get_pipeline_schema     required parameters and samplesheet columns
  → generate_samplesheet    samplesheet.csv from a folder of FASTQ files
  → validate_inputs         checked against the pipeline's own schemas → validation_id
  → launch                  nextflow run nf-core/<name> -r <release>, in the background → run_id
  → run_status              running / completed / failed, with the error report
  → run_summary             outputs, QC table, software versions, methods text, references
```

The server runs on your machine and talks to the client over stdio; there is no web server and no port. The pipelines also run on your machine. What a tool returns (pipeline names, schemas, file names, run status, QC numbers) goes to the language model; your sequencing reads never pass through a tool and stay on disk.

## Requirements

- macOS or Linux
- [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 and the dependencies for you)
- [Nextflow](https://www.nextflow.io/docs/latest/install.html) with Java 17 or later, to launch runs
- Docker to run pipelines with `-profile docker`. On macOS with [Colima](https://github.com/abiosoft/colima), start it with `colima start` before launching.
- An MCP client: [Claude Code](https://docs.claude.com/en/docs/claude-code) or [Claude Desktop](https://claude.ai/download)

Searching, reading docs and building samplesheets work without Nextflow and Docker; only `launch` needs them.

## Install

```bash
git clone https://github.com/Joon-Klaps/testing-nf-core-mcp.git
cd testing-nf-core-mcp
uv sync
uv run nfcore-mcp-build-index
```

`nfcore-mcp-build-index` downloads the nf-core catalog and the README of every pipeline listed in `shortlist.txt` at its latest release, and writes `index/pipelines.json`. Run it again after editing `shortlist.txt` or when new releases come out. Only pipelines in the index can be searched, inspected or launched.

Put the FASTQ files you want to use under `data/` in the repository, or point the server at other folders (see [Configuration](#configuration)). To try it with the small test data of nf-core/demo:

```bash
scripts/fetch_demo_data.sh    # four FASTQ files into data/demo
```

## Connect a client

Registering the server only changes configuration on your own machine. Nothing is uploaded or published: the client starts the server as a local process whenever it needs it.

### Claude Code

From the repository folder:

```bash
claude mcp add nf-core -- uv --directory "$(pwd)" run nfcore-mcp
```

This registers the server for you, in this project only (scope `local`, the default). Add `-s user` to have it in every project. Avoid `-s project` for this server: it writes a `.mcp.json` into the repository, with your absolute path in it, meant to be committed and shared.

Check that it connects with `claude mcp list`, or `/mcp` inside a session.

### Claude Desktop

Add the server to `~/Library/Application Support/Claude/claude_desktop_config.json` (create the file if it does not exist), then restart Claude Desktop:

```json
{
  "mcpServers": {
    "nf-core": {
      "command": "/opt/homebrew/bin/uv",
      "args": ["--directory", "/absolute/path/to/testing-nf-core-mcp", "run", "nfcore-mcp"]
    }
  }
}
```

Use full paths: Claude Desktop starts servers with a minimal `PATH`. `which uv` gives the path to `uv` on your machine.

### MCP Inspector

To call the tools by hand, without a language model:

```bash
uv run mcp dev src/nfcore_mcp/server.py
```

## Tools

| Tool | What it does | Changes anything? |
| --- | --- | --- |
| `search_pipelines(problem, k=5)` | Ranks indexed pipelines against a plain-language problem description (BM25 over name, description, topics and README) and returns the README lines that matched. | No |
| `get_pipeline_docs(name, release, doc="usage")` | Fetches a pipeline's `readme`, `usage` or `output` page at a pinned release. | No |
| `get_pipeline_schema(name, release)` | Lists the required parameters, and the samplesheet columns with which are required and which file names they accept. | No |
| `generate_samplesheet(name, release, input_dir, extra_columns=None)` | Pairs read 1 and read 2 by file name, puts them in the columns the pipeline's samplesheet schema asks for, fills `extra_columns` (for example `{"strandedness": "auto"}`), validates every row, and writes the CSV under `runs/samplesheets/`. Reports required columns it could not fill and files it could not place; it never guesses a value. | Writes a CSV |
| `validate_inputs(name, release, params, samplesheet_path)` | Checks parameters and samplesheet against the pipeline's `nextflow_schema.json` and samplesheet schema, and answers in plain sentences ("Row 2, sample: Sample name must be provided and cannot contain spaces"). A pass returns a `validation_id`. | No |
| `launch(name, release, validation_id, profile="test,docker,emulate_amd64")` | Starts `nextflow run nf-core/<name> -r <release>` in the background in its own folder under `runs/`, and returns a `run_id` at once. | Starts a run |
| `run_status(run_id)` | Running, completed, failed or stopped, with task counts, failed tasks and Nextflow's error report. | No |
| `run_summary(run_id)` | For a completed run: the output folders, MultiQC's per-sample statistics, software versions, the parameters set, and the pipeline's own methods text and references from its MultiQC report. | No |

The resource `nfcore://catalog` holds every indexed pipeline with its release, description and topics.

## Example

Ask Claude something like:

> I have paired-end Illumina reads in data/demo. Which nf-core pipeline should I use for a quick QC, and can you run it with the test profile?

Claude searches, reads the schema, builds and validates a samplesheet, asks your approval to launch, and reports back as the run progresses. Claude Code and Claude Desktop ask you to approve tool calls, so nothing is written or launched without your say.

## Guardrails

The server, not the model, decides what may run. The model never gets a shell, and these rules hold whichever client or model is connected:

- Only pipelines in the local index, and only at the indexed release: never `dev`, never an arbitrary repository.
- No launch without a `validation_id` from a passing `validate_inputs` for the same pipeline and release. The id is tied to the samplesheet's SHA-256, so a samplesheet edited after validation is refused.
- The server sets `input` and `outdir` itself; the model cannot choose where results are written.
- Parameters the pipeline's schema does not know are rejected, which catches invented parameters.
- Profiles come from an allow list: `test`, `docker`, `emulate_amd64`, `arm64`, `singularity`, `conda`.
- Data paths must be inside the allowed data folders, judged after resolving `..` and symlinks.
- Every call, refused ones included, is appended to `runs/audit.log`, one JSON object per line.

## Configuration

Set these as environment variables: in your shell for Claude Code and the Inspector, with `-e KEY=value` in `claude mcp add`, or in an `"env": {...}` block next to `"args"` in the Claude Desktop config.

| Variable | Default | Meaning |
| --- | --- | --- |
| `NFCORE_MCP_DATA_DIRS` | `<repo>/data` | Folders the server may read data from, separated by `:` |
| `NFCORE_MCP_NEXTFLOW` | `nextflow` | Path to the Nextflow executable |
| `NFCORE_MCP_HOME` | the repository | Where `index/`, `runs/` and `shortlist.txt` live |

## Where things go

```text
index/pipelines.json          the search index (built by nfcore-mcp-build-index)
index/schemas/                schemas, cached per pipeline release
runs/samplesheets/            samplesheets written by generate_samplesheet
runs/<run_id>/                one folder per launch: run.json, params.yaml, nextflow.out, trace.txt, work/, results/
runs/audit.log                every tool call
```

`index/`, `runs/` and `data/` are not tracked by git.

## Development

```bash
uv run pytest                          # offline tests, no network or Docker needed
uv run mcp dev src/nfcore_mcp/server.py
```

The code is in `src/nfcore_mcp/`, one job per module: `server.py` holds the MCP tools and nothing else, `catalog.py` is the only module that does HTTP, `guardrails.py` holds every rule the server enforces, and `schemas.py`, `validation.py`, `samplesheet.py`, `runs.py`, `summary.py` and `audit.py` do the work behind the tools.

## Status

A learning project. Search, schemas, samplesheets and validation have been tested against real nf-core schemas; launching and run monitoring have been tested against real Nextflow output, but a full nf-core run through the tools has not been done yet. Runs are local only: no HPC, no remote transport, a single user.
