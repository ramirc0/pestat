"""Attach jobs to nodes, flag unexpected node states and select nodes."""

from pestat.hostlist import expand

RED, MAGENTA = "red", "magenta"
RED_STATES = {
    "boot",
    "comp",
    "down",
    "drain",
    "drng",
    "fail",
    "failg",
    "inval",
    "maint",
    "resv",
    "unk",
}
STATE_SUFFIXES = "*~#!%$@^-"


def build(nodes, jobs, opts):
    """Attach jobs to nodes, flag each node and keep the selected ones.

    Parameters
    ----------
    nodes : dict of str to dict
        Node records from :func:`pestat.slurm.parse_nodes`. They gain
        ``jobs`` and ``flags``.
    jobs : list of dict
        Job records from :func:`pestat.slurm.parse_jobs`.
    opts : argparse.Namespace
        Options from :func:`pestat.cli.build_parser`.

    Returns
    -------
    list of dict
        Selected nodes in sinfo order.

    Examples
    --------
    >>> from pestat.cli import build_parser
    >>> from pestat.slurm import parse_jobs, parse_nodes
    >>> nodes, _ = parse_nodes(
    ...     [
    ...         "z1|long|4/4/0/8|9.50|64000|60000|mix|1|(null)|gpu:0|none",
    ...         "z2|long|0/8/0/8|0.00|64000|60000|idle|1|(null)|gpu:0|none",
    ...     ]
    ... )
    >>> jobs = parse_jobs(["7|7|0|0|a|s|e|1:00:00|long|1|cpu=4|z1|x"])
    >>> [(n["hostname"], n["flags"]) for n in build(nodes, jobs, build_parser().parse_args(["-f"]))]
    [('z1', {'state': None, 'load': 'red', 'jobs': None, 'memory': None})]
    """
    if opts.groupname:
        jobs = [job for job in jobs if job["group"] in opts.groupname]
    for job in jobs:
        for host in expand(job["nodelist"]):
            if host in nodes:
                nodes[host]["jobs"].append(job)
    selection = opts.accountlist or opts.qoslist or opts.reservationlist
    rows = []
    for node in nodes.values():
        node["flags"] = flags(node, opts)
        if _hidden(node, opts) or (selection or opts.groupname) and not node["jobs"]:
            continue
        if opts.username and not any(
            job["user"] in opts.username for job in node["jobs"]
        ):
            continue
        if opts.omitdown and any(state in node["state"] for state in opts.statedown):
            continue
        if _listed(node, opts):
            rows.append(node)
    return rows


def flags(node, opts):
    """Flag a node's state, CPU load, job count and free memory.

    The bounds follow upstream pestat. The ideal load runs from the
    allocated cores divided by the threads per core (or the GPUs in use)
    up to the allocated cores (or the node's share of cores for the GPUs
    in use). A node whose newest job started less than ``jobgracetime``
    seconds ago gets no load flag, because the load average still lags.

    Parameters
    ----------
    node : dict
        Node record with its ``jobs`` attached.
    opts : argparse.Namespace
        Thresholds ``cpuload_delta1/2``, ``memory_thres1/2`` and
        ``jobgracetime``.

    Returns
    -------
    dict
        ``state``, ``load``, ``jobs`` and ``memory``, each None, ``"red"`` or
        ``"magenta"``.

    Examples
    --------
    >>> from pestat.cli import build_parser
    >>> node = {
    ...     "state": "down*",
    ...     "cpus_alloc": 0,
    ...     "cpus_total": 8,
    ...     "cpu_load": None,
    ...     "threads_per_core": 1,
    ...     "memory_mb": 64000,
    ...     "free_mem_mb": 9000,
    ...     "gpus_total": 0,
    ...     "gpus_used": 0,
    ...     "jobs": [],
    ... }
    >>> flags(node, build_parser().parse_args([]))
    {'state': 'red', 'load': None, 'jobs': None, 'memory': 'magenta'}
    """
    base = node["state"].rstrip(STATE_SUFFIXES)
    alloc, jobs = node["cpus_alloc"], node["jobs"]
    if len(jobs) > alloc:
        jobs_flag = RED
    elif base == "mix" and any((job["num_nodes"] or 0) > 1 for job in jobs):
        jobs_flag = MAGENTA
    else:
        jobs_flag = None
    return {
        "state": RED if base in RED_STATES or "*" in node["state"] else None,
        "load": _load_flag(node, opts),
        "jobs": jobs_flag,
        "memory": _level(
            node["free_mem_mb"],
            node["memory_mb"] * opts.memory_thres1,
            node["memory_mb"] * opts.memory_thres2,
        ),
    }


def _load_flag(node, opts):
    """Flag a CPU load outside the ideal range for the node's allocation.

    Parameters
    ----------
    node : dict
        Node record with its ``jobs`` attached.
    opts : argparse.Namespace
        Thresholds ``cpuload_delta1/2`` and ``jobgracetime``.

    Returns
    -------
    str or None
        ``"red"``, ``"magenta"`` or None.
    """
    load = node["cpu_load"]
    used = [
        job["time_used_s"] for job in node["jobs"] if job["time_used_s"] is not None
    ]
    if load is None or (used and min(used) < opts.jobgracetime):
        return None
    alloc, threads = node["cpus_alloc"], node["threads_per_core"] or 1
    high = alloc
    if node["gpus_used"] and node["gpus_total"]:
        low = node["gpus_used"]
        high = node["cpus_total"] * node["gpus_used"] / node["gpus_total"]
    else:
        low = alloc / threads
    for level, delta in ((RED, opts.cpuload_delta1), (MAGENTA, opts.cpuload_delta2)):
        if load > high + delta or load < low - delta:
            return level
    return None


def _level(free, red, magenta):
    """Flag free memory below the RED or MAGENTA threshold.

    Parameters
    ----------
    free : int or None
        Free memory in MB.
    red, magenta : float
        Thresholds in MB.

    Returns
    -------
    str or None
        ``"red"``, ``"magenta"`` or None.
    """
    if free is None:
        return None
    if free < red:
        return RED
    if free < magenta:
        return MAGENTA
    return None


def _hidden(node, opts):
    """Tell whether a node is powered down or in maintenance.

    Upstream pestat always hides these. Here they show when ``--states``
    asks for them.

    Parameters
    ----------
    node : dict
        Node record.
    opts : argparse.Namespace
        Options with ``statelist``.

    Returns
    -------
    bool
        True if the node is skipped.
    """
    state = node["state"]
    return not opts.statelist and (
        state in ("idle~", "drain~") or state.rstrip(STATE_SUFFIXES) == "maint"
    )


def _listed(node, opts):
    """Apply the ``-f``, ``-F``, ``-m`` and ``-M`` listing filters.

    Parameters
    ----------
    node : dict
        Node record with ``flags``.
    opts : argparse.Namespace
        Options with ``free_mem_less``, ``free_mem_more`` and ``flaglevel``.

    Returns
    -------
    bool
        True if the node is printed.
    """
    free = node["free_mem_mb"]
    if opts.free_mem_less:
        return free is not None and free < opts.free_mem_less
    if opts.free_mem_more:
        return free is not None and free > opts.free_mem_more
    if opts.flaglevel:
        levels = set(node["flags"].values())
        return RED in levels or (opts.flaglevel == 1 and MAGENTA in levels)
    return True
