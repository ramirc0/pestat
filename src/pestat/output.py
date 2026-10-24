"""Write node status as a text table, JSON, TSV or CSV."""

PAINT = {
    "bold": "\033[1m",
    "green": "\033[1;32m",
    "magenta": "\033[1;35m",
    "red": "\033[1;31m",
}
RESET = "\033[0m"
MARK = {"red": "!", "magenta": "*", None: " "}
TABLE_FIELDS = (
    "hostname",
    "partitions",
    "state",
    "reason",
    "cpus_alloc",
    "cpus_idle",
    "cpus_other",
    "cpus_total",
    "cpu_load",
    "threads_per_core",
    "memory_mb",
    "free_mem_mb",
    "gres",
    "gres_used",
    "gpus_total",
    "gpus_used",
)


def write_text(rows, opts, file, *, default_partition=None, colors=False, width=None):
    """Write nodes as an aligned table with a summary line.

    Flagged cells get a ``!`` (critical) or ``*`` (warning) mark and, with
    colors, a red or magenta paint. The job count flag marks the CPUS cell.

    Parameters
    ----------
    rows : list of dict
        Nodes from :func:`pestat.status.build`.
    opts : argparse.Namespace
        Display options: ``uniquenodes``, ``printgres``, ``printaccount``,
        ``printjobname``, ``printjobstarttime``, ``printjobendtime`` and
        ``printtimeused``.
    file : file-like
        Text stream to write to.
    default_partition : str, optional
        Partition to mark with ``*``.
    colors : bool, optional
        Emit ANSI colors.
    width : int, optional
        Cut each line to this many characters inside the JOBS column.

    Examples
    --------
    >>> import sys
    >>> from pestat.cli import build_parser
    >>> node = {
    ...     "hostname": "z1",
    ...     "partitions": ["short", "long"],
    ...     "state": "mix",
    ...     "reason": None,
    ...     "cpus_alloc": 4,
    ...     "cpus_total": 8,
    ...     "cpu_load": 9.5,
    ...     "memory_mb": 65536,
    ...     "free_mem_mb": 61440,
    ...     "gpus_total": 0,
    ...     "gpus_used": 0,
    ...     "flags": {"state": None, "load": "red", "jobs": None, "memory": None},
    ...     "jobs": [{"job_id": "7", "array_id": None, "user": "root"}],
    ... }
    >>> opts = build_parser().parse_args([])
    >>> write_text([node], opts, sys.stdout, default_partition="short")
    HOST  PARTITIONS   STATE   CPUS   LOAD(5m)   MEMORY  FREE   JOBS
    z1    short*,long  mix      4/8       9.50!     64G   60G   root 7
    <BLANKLINE>
    1 node, 4/8 CPUs allocated, 1 critical (!)
    """
    gpus = any(node["gpus_total"] for node in rows)
    table = [_header(opts, gpus)]
    for node in rows:
        groups = (
            [node["partitions"]]
            if opts.uniquenodes
            else [[p] for p in node["partitions"]]
        )
        table.extend(
            _cells(node, parts, opts, gpus, default_partition) for parts in groups
        )
    widths = [
        max(len(cells[i][0]) for cells, _ in table) for i in range(len(table[0][0]))
    ]
    ellipsis = (
        "…" if "utf" in (getattr(file, "encoding", None) or "").lower() else "..."
    )
    for cells, jobs in table:
        plain, painted = [], []
        for (text, color, right), size in zip(cells, widths, strict=True):
            pad = " " * (size - len(text))
            shown = PAINT[color] + text + RESET if colors and color else text
            plain.append(pad + text if right else text + pad)
            painted.append(pad + shown if right else shown + pad)
        room = width - len("  ".join(plain)) - 2 if width else len(jobs)
        if jobs and len(jobs) > room:
            jobs = jobs[: max(room - len(ellipsis), 0)] + ellipsis
        file.write(("  ".join(painted) + "  " + jobs).rstrip() + "\n")
    file.write("\n" + _summary(rows) + "\n")


def _header(opts, gpus):
    """Build the header cells.

    Parameters
    ----------
    opts : argparse.Namespace
        Display options.
    gpus : bool
        Include the GPUS column.

    Returns
    -------
    cells : list of tuple
        ``(text, color, right_aligned)`` cells. Markable columns end in a
        space so the label lines up with the values.
    jobs : str
        The JOBS label, naming the per-job extras.
    """
    extras = [
        name
        for name, on in (
            ("gpu", opts.printgres),
            ("name", opts.printjobname),
            ("start", opts.printjobstarttime),
            ("end", opts.printjobendtime),
            ("used", opts.printtimeused),
        )
        if on
    ]
    jobs = "JOBS"
    if extras or opts.printaccount:
        user = "user(account)" if opts.printaccount else "user"
        jobs += f" {user} id" + (f"({','.join(extras)})" if extras else "")
    cells = [
        ("HOST", False),
        ("PARTITIONS", False),
        ("STATE ", False),
        ("CPUS ", True),
        ("LOAD(5m) ", True),
        ("MEMORY", True),
        ("FREE ", True),
    ]
    if gpus:
        cells.append(("GPUS", True))
    return [(text, "bold", right) for text, right in cells], jobs


def _cells(node, partitions, opts, gpus, default_partition):
    """Build one row of cells for a node.

    Parameters
    ----------
    node : dict
        Node record with ``flags`` and ``jobs``.
    partitions : list of str
        Partitions to show on this line.
    opts : argparse.Namespace
        Display options.
    gpus : bool
        Include the GPUS column.
    default_partition : str or None
        Partition to mark with ``*``.

    Returns
    -------
    cells : list of tuple
        ``(text, color, right_aligned)`` cells before JOBS.
    jobs : str
        The JOBS text.
    """
    flags = node["flags"]
    load = node["cpu_load"]
    state_color = flags["state"] or ("green" if node["state"] == "idle" else None)
    cells = [
        (node["hostname"], None, False),
        (",".join(p + "*" * (p == default_partition) for p in partitions), None, False),
        (node["state"] + MARK[flags["state"]], state_color, False),
        (
            f"{node['cpus_alloc']}/{node['cpus_total']}" + MARK[flags["jobs"]],
            flags["jobs"],
            True,
        ),
        (
            ("N/A" if load is None else f"{load:.2f}") + MARK[flags["load"]],
            flags["load"],
            True,
        ),
        (_size(node["memory_mb"]), None, True),
        (_size(node["free_mem_mb"]) + MARK[flags["memory"]], flags["memory"], True),
    ]
    if gpus:
        text = f"{node['gpus_used']}/{node['gpus_total']}" if node["gpus_total"] else ""
        cells.append((text, None, True))
    return cells, _jobs(node, opts)


def _jobs(node, opts):
    """Describe a node's reason and jobs, grouped by user.

    Parameters
    ----------
    node : dict
        Node record with ``reason`` and ``jobs``.
    opts : argparse.Namespace
        Display options.

    Returns
    -------
    str
        For example ``[Not responding]`` or ``alice 12 13, bob 14``.
    """
    groups = {}
    for job in node["jobs"]:
        user = job["user"]
        if opts.printaccount:
            user += f"({job['account'] or '-'})"
        groups.setdefault(user, []).append(_job_label(job, opts))
    text = ", ".join(f"{user} {' '.join(ids)}" for user, ids in groups.items())
    if opts.printjobendtime and len(node["jobs"]) > 1:
        ends = [job["end_time"] for job in node["jobs"] if job["end_time"]]
        text += f", last end {max(ends)}" if ends else ""
    if node["reason"]:
        text = f"[{node['reason']}] {text}".rstrip()
    return text


def _job_label(job, opts):
    """Label a job with its ID and the requested extras.

    Parameters
    ----------
    job : dict
        Job record.
    opts : argparse.Namespace
        Display options.

    Returns
    -------
    str
        ``id`` or ``id(extra,...)``. Array tasks use squeue's ``base_task`` form.
    """
    extras = []
    if opts.printgres:
        tres = [
            e
            for e in job["tres_alloc"].split(",")
            if e.startswith(("gres/gpu=", "gres/gpu:"))
        ]
        extras.append(tres[-1].removeprefix("gres/") if tres else "-")
    if opts.printjobname:
        extras.append(job["name"] or "-")
    if opts.printjobstarttime:
        extras.append(job["start_time"] or "-")
    if opts.printjobendtime:
        extras.append(job["end_time"] or "-")
    if opts.printtimeused:
        extras.append(job["time_used"])
    label = job["array_id"] or job["job_id"]
    return f"{label}({','.join(extras)})" if extras else label


def _size(mb):
    """Format a size in MB as GiB, or MB below 1 GiB.

    Parameters
    ----------
    mb : int or None
        Size in MB.

    Returns
    -------
    str
        For example ``977G``, ``512M`` or ``N/A``.
    """
    if mb is None:
        return "N/A"
    return f"{mb}M" if mb < 1024 else f"{mb / 1024:.0f}G"


def _summary(rows):
    """Summarize the listed nodes in one line.

    Parameters
    ----------
    rows : list of dict
        Listed nodes.

    Returns
    -------
    str
        Node, CPU and GPU totals and flag counts.
    """
    parts = [
        _plural(len(rows), "node"),
        f"{sum(n['cpus_alloc'] for n in rows)}/{sum(n['cpus_total'] for n in rows)} CPUs allocated",
    ]
    gpus = sum(n["gpus_total"] for n in rows)
    if gpus:
        parts.append(f"{sum(n['gpus_used'] for n in rows)}/{gpus} GPUs in use")
    levels = [set(n["flags"].values()) for n in rows]
    critical = sum("red" in s for s in levels)
    warning = sum("magenta" in s and "red" not in s for s in levels)
    if critical:
        parts.append(f"{critical} critical (!)")
    if warning:
        parts.append(_plural(warning, "warning") + " (*)")
    return ", ".join(parts)


def _plural(count, noun):
    """Pair a count with a noun, adding ``s`` when needed.

    Parameters
    ----------
    count : int
        Count.
    noun : str
        Singular noun.

    Returns
    -------
    str
        For example ``1 node`` or ``3 nodes``.
    """
    return f"{count} {noun}" + ("" if count == 1 else "s")


def write_json(rows, file, *, default_partition=None):
    """Write nodes and their jobs as one JSON document.

    Parameters
    ----------
    rows : list of dict
        Nodes from :func:`pestat.status.build`.
    file : file-like
        Text stream to write to.
    default_partition : str, optional
        The cluster's default partition.

    Examples
    --------
    >>> import io, json
    >>> out = io.StringIO()
    >>> write_json([{"hostname": "z1"}], out, default_partition="short")
    >>> doc = json.loads(out.getvalue())
    >>> doc["default_partition"], doc["nodes"]
    ('short', [{'hostname': 'z1'}])
    """
    import json
    import time

    doc = {
        "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "default_partition": default_partition,
        "nodes": rows,
    }
    json.dump(doc, file, indent=2)
    file.write("\n")


def write_table(rows, file, delimiter):
    r"""Write one delimited row per node, with a header.

    Lists are joined with commas, None becomes an empty cell, and each
    flag gets its own column. ``job_ids`` and ``users`` list the jobs in
    the same order.

    Parameters
    ----------
    rows : list of dict
        Nodes from :func:`pestat.status.build`.
    file : file-like
        Text stream to write to.
    delimiter : str
        ``"\t"`` for TSV or ``","`` for CSV.

    Examples
    --------
    >>> import sys
    >>> node = dict.fromkeys(TABLE_FIELDS, 0)
    >>> node.update(hostname="z1", partitions=["a", "b"], reason=None)
    >>> node["flags"] = {"state": None, "load": "red", "jobs": None, "memory": None}
    >>> node["jobs"] = [{"job_id": "7", "array_id": None, "user": "root"}]
    >>> write_table([node], sys.stdout, ",")  # doctest: +NORMALIZE_WHITESPACE
    hostname,partitions,state,reason,cpus_alloc,cpus_idle,cpus_other,cpus_total,cpu_load,threads_per_core,memory_mb,free_mem_mb,gres,gres_used,gpus_total,gpus_used,state_flag,load_flag,jobs_flag,memory_flag,job_ids,users
    z1,"a,b",0,,0,0,0,0,0,0,0,0,0,0,0,0,,red,,,7,root
    """
    import csv

    writer = csv.writer(file, delimiter=delimiter, lineterminator="\n")
    flag_names = ("state", "load", "jobs", "memory")
    writer.writerow(
        [*TABLE_FIELDS, *(f"{f}_flag" for f in flag_names), "job_ids", "users"]
    )
    for node in rows:
        values = [node[f] for f in TABLE_FIELDS] + [
            node["flags"][f] for f in flag_names
        ]
        values.append(
            ",".join(job["array_id"] or job["job_id"] for job in node["jobs"])
        )
        values.append(",".join(job["user"] for job in node["jobs"]))
        writer.writerow(
            [
                "" if v is None else ",".join(v) if isinstance(v, list) else v
                for v in values
            ]
        )
