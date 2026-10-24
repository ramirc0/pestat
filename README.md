# pestat

`pestat` prints one line per Slurm node with its partitions, state, CPU use, load, memory, GPUs, and running jobs. It flags nodes whose state, load, memory, or job count looks wrong. It is a Python port of Ole Holm Nielsen's [pestat](https://github.com/OleHolmNielsen/Slurm_tools/tree/master/pestat) (bash and awk, upstream commit `892324f`). It keeps the upstream flags and flagging rules, adds JSON, TSV and CSV output, and runs `sinfo` and `squeue` concurrently.

It needs Python 3.11 or later and has no dependencies.

## Install

```bash
pixi global install --git https://github.com/ramirc0/pestat.git --expose pestat --with python=3.13
```

`uv tool install --python 3.13 .` also works from a checkout. Python 3.14 and later make `argparse` import `_colorize`, which adds about 7 ms to each run, so pin 3.12 or 3.13 for the fastest start.

## Usage

```
$ pestat -p gpuh200,gpu,foobar -N
pestat: warning: no nodes matched partition 'foobar'
HOST  PARTITIONS  STATE     CPUS   LOAD(5m)   MEMORY  FREE   GPUS  JOBS user id(name)
z018  gpu         idle      0/32       0.22     342G  343G    0/4
z020  gpu         idle      0/32      13.75!    342G  366G    0/4
z028  gpuh200     mix     96/256       2.04*    977G  293G    6/8  phanh 3617294_11(run_cellpose.sh) 3617294_10(run_cellpose.sh), mjp 3564212(bash)

10 nodes, 96/544 CPUs allocated, 6/44 GPUs in use, 1 critical (!), 1 warning (*)
```

- `!` marks a critical flag and `*` a warning. On a terminal, the flagged cell is red or magenta.
- `CPUS` is allocated/total. `LOAD(5m)` is the 5-minute load average that slurmd reports.
- `MEMORY` and `FREE` are in GiB. The `-m`/`-M` thresholds are in MB, as upstream.
- `PARTITIONS` lists every partition of the node in slurm.conf order, with `*` on the default partition.
- `JOBS` starts with the node's `[reason]` when it has one. It then lists job IDs grouped by user, with array tasks as `base_task`.
- On a terminal, long lines are cut to the window width. Pipes get full lines.

Run `pestat -h` for every option. All upstream short flags work. `-p`, `-u` and `-g` take comma-separated lists.

### Flags

| Mark | Cell | Rule |
| --- | --- | --- |
| `!` | `STATE` | The base state is drain, drng, down, fail, failg, inval, unk, resv, comp, boot or maint, or the node is not responding (`*`). |
| `!` / `*` | `LOAD(5m)` | The load is more than 4 (`!`) or 2 (`*`) outside the ideal range. The range runs from allocated CPUs divided by threads per core (or the GPUs in use) up to the allocated CPUs (or the node's CPU share of the GPUs in use). There is no load flag within 900 s of a job start. |
| `!` / `*` | `FREE` | Free memory is below 10% (`!`) or 20% (`*`) of the node's memory. |
| `!` / `*` | `CPUS` | More jobs than allocated CPUs (`!`), or a multi-node job on a `mix` node (`*`). |

`-f` lists flagged nodes. `-F` lists nodes with a `!`.

### Machine-readable output

`-o json` writes one document: `{"time", "default_partition", "nodes": [...]}`. Each node holds its numbers, a `flags` object (`null`, `"red"` or `"magenta"` per cell) and its `jobs`.

`-o tsv` and `-o csv` write one row per node with a header. List fields are joined with commas, and each flag gets its own column. All formats apply the same filters. The display options (`-G -N -S -E -T -a`, `-1/-2`, colors) only change the text table.

```bash
pestat -o json | jq -r '.nodes[] | select(.gpus_used < .gpus_total) | .hostname'
pestat -o tsv -f | mlr --itsv --opprint cut -f hostname,state,load_flag,memory_flag
```

### Configuration

`/etc/pestat.conf` and then `~/.pestat.conf` set defaults as `NAME=value` lines, with the upstream variable names (`cpuload_delta1`, `memory_thres1`, `jobgracetime`, `colors`, `uniquenodes`, `partition`, …). `export`, quotes, comments and `$VAR` work. The files are parsed, not run by a shell. Unknown lines print a warning.

## Differences from upstream pestat

- The text table is new.
  - Every partition of a node is listed.
  - Memory is in GiB, and the `GPUS` column appears whenever GPU nodes are listed.
  - Jobs are grouped by user, and the drain or down reason is shown.
  - Lines fit the terminal, and a summary line ends the table.
- Bugs fixed:
  - Untyped GRES (`gpu:4`) counted 0 GPUs, and GRES with `(S:0-1)` crashed gawk.
  - Job names were cut at the first space, and fields of 30 or more characters shifted the columns.
  - `-u` rejected names with `.`, `_` or `-`.
  - Array tasks lost their array ID.
  - The default-partition `*` landed on the wrong partition.
  - `-2 -E` repeated `LASTJOB`.
  - A job at `0:00` skipped the grace period.
  - `N/A` load or memory counted as 0.
  - `-F` listed warning-only nodes.
  - Jobs in hidden partitions were missing.
  - `SQUEUE_*`/`SINFO_*` and stray shell variables changed the output.
  - `-t maint` printed nothing.
  - `-n` and `-j` overwrote each other; they now intersect.
  - A failing `squeue` went unnoticed.
  - `-m 0` acted like `-F`.
  - Hostlists were globbed against the working directory.
- Behavior changes:
  - States match on their base name, so `down*` and `drain$` are flagged.
  - The grace period hides only the load flag.
  - Flags also show under `-m`/`-M`.
  - Informational notices are gone, and warnings and errors go to stderr. Exit status is 0, 1 for runtime errors, or 2 for usage errors.
  - `-u`/`-g` fail on unknown names. `-A`/`-q`/`-R` warn when no job matched instead of calling `sacctmgr`.
  - Unknown partitions in `-p` print a warning.
  - The load header says 5 minutes, because slurmd reports `loads[1]` of `sysinfo`.

## Development

```bash
env -u UV_EXCLUDE_NEWER uv sync     # the shell's UV_EXCLUDE_NEWER would rewrite uv.lock
.venv/bin/ruff check && .venv/bin/ruff format --check
.venv/bin/numpydoc lint src/pestat/*.py
.venv/bin/pytest                    # doctests, unit tests, parity with tests/reference/pestat
.venv/bin/pytest -m live            # against the live cluster
.venv/bin/pytest --update-golden    # rewrite tests/golden after a deliberate layout change
```

The parity tests run the upstream script and pestat on the same fake cluster. `tests/fakeslurm.py` serves a JSON snapshot as `sinfo`, `squeue`, `scontrol` and `sacctmgr`. They need bash and GNU awk.

## License

GPL-3.0-only, as upstream.
