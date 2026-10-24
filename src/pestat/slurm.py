"""Query sinfo and squeue concurrently and parse their output."""

import functools
import grp
import os
import pwd
import re
import subprocess

SINFO_FIELDS = (
    "NodeList",
    "Partition",
    "CPUsState",
    "CPUsLoad",
    "Memory",
    "FreeMem",
    "StateCompact",
    "Threads",
    "Gres",
    "GresUsed",
    "Reason",
)
SQUEUE_FIELDS = (
    "JobID",
    "JobArrayID",
    "UserID",
    "GroupID",
    "Account",
    "StartTime",
    "EndTime",
    "TimeUsed",
    "Partition",
    "NumNodes",
    "tres-alloc",
    "NodeList",
    "Name",
)
_MISSING = ("", "(null)", "N/A", "Unknown", "None")


def _format(fields):
    """Build an ``-O`` spec of unpadded ``|``-separated fields.

    Parameters
    ----------
    fields : tuple of str
        Field names. The last one may contain ``|``, so it takes no suffix.

    Returns
    -------
    str
        The ``-O`` argument.
    """
    return ",".join(f"{field}:|" for field in fields[:-1]) + f",{fields[-1]}:"


def sinfo_command(
    *, partitions=(), nodes=None, states=None, all_partitions=False, prefix=""
):
    """Build the sinfo command that lists one row per node and partition.

    Parameters
    ----------
    partitions : sequence of str, optional
        Partition names. sinfo shows hidden partitions when they are named.
    nodes : str, optional
        Slurm hostlist to restrict the nodes.
    states : str, optional
        Comma-separated node states.
    all_partitions : bool, optional
        Include hidden and unavailable partitions.
    prefix : str, optional
        Directory of the Slurm commands. Empty means a PATH lookup.

    Returns
    -------
    list of str
        The argv. ``-S N,#P`` lists each node's partitions in config order.

    Examples
    --------
    >>> sinfo_command(partitions=["gpu", "long"])[:7]
    ['sinfo', '-h', '-N', '-S', 'N,#P', '-p', 'gpu,long']
    """
    argv = [os.path.join(prefix, "sinfo"), "-h", "-N", "-S", "N,#P"]
    if all_partitions:
        argv.append("--all")
    if partitions:
        argv += ["-p", ",".join(partitions)]
    if nodes:
        argv += ["-n", nodes]
    if states:
        argv += ["--states", states]
    return argv + ["-O", _format(SINFO_FIELDS)]


def squeue_command(*, accounts=None, qos=None, reservations=None, prefix=""):
    """Build the squeue command that lists running jobs.

    ``--all`` keeps jobs from hidden partitions, because a visible node can
    run them.

    Parameters
    ----------
    accounts, qos, reservations : str, optional
        Comma-separated selections passed to squeue.
    prefix : str, optional
        Directory of the Slurm commands. Empty means a PATH lookup.

    Returns
    -------
    list of str
        The argv.

    Examples
    --------
    >>> squeue_command(accounts="a1")[:7]
    ['squeue', '-h', '--all', '-t', 'RUNNING', '-A', 'a1']
    """
    argv = [os.path.join(prefix, "squeue"), "-h", "--all", "-t", "RUNNING"]
    for flag, value in (("-A", accounts), ("-q", qos), ("-R", reservations)):
        if value:
            argv += [flag, value]
    return argv + ["-O", _format(SQUEUE_FIELDS)]


def job_nodes_command(job_ids, prefix=""):
    """Build the squeue command that lists the nodes of running jobs.

    Parameters
    ----------
    job_ids : str
        Comma-separated job IDs. A base array ID selects all its tasks.
    prefix : str, optional
        Directory of the Slurm commands. Empty means a PATH lookup.

    Returns
    -------
    list of str
        The argv. It prints one NodeList per job.

    Examples
    --------
    >>> job_nodes_command("123,456")
    ['squeue', '-h', '--all', '-t', 'running', '-j', '123,456', '-O', 'NodeList:']
    """
    return [os.path.join(prefix, "squeue"), "-h", "--all", "-t", "running"] + [
        "-j",
        job_ids,
        "-O",
        "NodeList:",
    ]


def run(commands):
    """Run commands concurrently and wait for all of them.

    The children get an environment without ``SQUEUE_*``, ``SINFO_*`` and
    ``SLURM_CLUSTERS``, which would filter or reformat the output, and
    with ISO timestamps.

    Parameters
    ----------
    commands : list of list of str
        Argv lists.

    Returns
    -------
    list of subprocess.CompletedProcess
        One result per command, with text ``stdout`` and ``stderr``.

    Raises
    ------
    FileNotFoundError
        If a command does not exist.

    Examples
    --------
    >>> results = run([["printf", "a|b"], ["sh", "-c", "exit 3"]])
    >>> [(r.returncode, r.stdout) for r in results]
    [(0, 'a|b'), (3, '')]
    """
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("SQUEUE_", "SINFO_")) and key != "SLURM_CLUSTERS"
    }
    env["SLURM_TIME_FORMAT"] = "standard"
    procs = [
        subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        for argv in commands
    ]
    results = []
    for proc in procs:
        out, err = proc.communicate()
        results.append(
            subprocess.CompletedProcess(proc.args, proc.returncode, out, err)
        )
    return results


def parse_nodes(lines):
    """Parse sinfo rows into node records, merging partition rows.

    Parameters
    ----------
    lines : iterable of str
        Output of :func:`sinfo_command`, one row per node and partition.

    Returns
    -------
    nodes : dict of str to dict
        Node records keyed by hostname, in sinfo order. Unknown values
        such as ``N/A`` become None.
    default_partition : str or None
        The partition sinfo marks with ``*``, if any row shows it.

    Examples
    --------
    >>> nodes, default = parse_nodes(
    ...     [
    ...         "z1|short*|8/0/0/8|7.90|64000|1000|alloc|1|(null)|gpu:0|none",
    ...         "z1|long|8/0/0/8|7.90|64000|1000|alloc|1|(null)|gpu:0|none",
    ...     ]
    ... )
    >>> nodes["z1"]["partitions"], default, nodes["z1"]["cpus_alloc"]
    (['short', 'long'], 'short', 8)
    """
    nodes, default = {}, None
    for line in lines:
        (
            host,
            partition,
            cpus,
            load,
            memory,
            free,
            state,
            threads,
            gres,
            gres_used,
            reason,
        ) = line.split("|", len(SINFO_FIELDS) - 1)
        if partition.endswith("*"):
            partition = default = partition[:-1]
        if host in nodes:
            nodes[host]["partitions"].append(partition)
            continue
        alloc, idle, other, total = (int(n) for n in cpus.split("/"))
        nodes[host] = {
            "hostname": host,
            "partitions": [partition],
            "state": state,
            "reason": None if reason in _MISSING or reason == "none" else reason,
            "cpus_alloc": alloc,
            "cpus_idle": idle,
            "cpus_other": other,
            "cpus_total": total,
            "cpu_load": _number(load, float),
            "threads_per_core": _number(threads, int),
            "memory_mb": _number(memory, int),
            "free_mem_mb": _number(free, int),
            "gres": None if gres in _MISSING else gres,
            "gres_used": None if gres_used in _MISSING else gres_used,
            "gpus_total": gpu_count(gres),
            "gpus_used": gpu_count(gres_used),
            "flags": {},
            "jobs": [],
        }
    return nodes, default


def parse_jobs(lines):
    """Parse squeue rows into job records.

    Parameters
    ----------
    lines : iterable of str
        Output of :func:`squeue_command`.

    Returns
    -------
    list of dict
        Job records in squeue order. User and group IDs resolve to names
        when the system knows them.

    Examples
    --------
    >>> [job] = parse_jobs(
    ...     [
    ...         "7|5_2|0|0|(null)|2026-10-24T08:00:00|2026-10-25T08:00:00"
    ...         "|1-02:03:04|long|1|cpu=4,gres/gpu=2|z[1-2]|a | b"
    ...     ]
    ... )
    >>> job["array_id"], job["user"], job["time_used_s"], job["gpus"], job["name"]
    ('5_2', 'root', 93784, 2, 'a | b')
    """
    jobs = []
    for line in lines:
        (
            job_id,
            array_id,
            uid,
            gid,
            account,
            start,
            end,
            used,
            partition,
            num_nodes,
            tres,
            nodelist,
            name,
        ) = line.split("|", len(SQUEUE_FIELDS) - 1)
        jobs.append(
            {
                "job_id": job_id,
                "array_id": None if array_id == job_id else array_id,
                "user": _user_name(uid),
                "group": _group_name(gid),
                "account": None if account in _MISSING else account,
                "partition": partition,
                "num_nodes": _number(num_nodes, int),
                "start_time": None if start in _MISSING else start,
                "end_time": None if end in _MISSING else end,
                "time_used": used,
                "time_used_s": _seconds(used),
                "name": name,
                "tres_alloc": tres,
                "gpus": _tres_gpus(tres),
                "nodelist": nodelist,
            }
        )
    return jobs


def gpu_count(gres):
    """Sum the GPUs in a Slurm GRES string.

    Parenthesized parts such as ``(S:0-1)`` or ``(IDX:0,2)`` are dropped
    before the comma split.

    Parameters
    ----------
    gres : str
        ``Gres`` or ``GresUsed`` value from sinfo.

    Returns
    -------
    int
        Number of GPUs, 0 when there are none.

    Examples
    --------
    >>> gpu_count("gpu:a100:2(IDX:0,2),gpu:v100:1(IDX:0),shard:8")
    3
    >>> gpu_count("gpu:4"), gpu_count("(null)")
    (4, 0)
    """
    total = 0
    for entry in re.sub(r"\([^)]*\)", "", gres).split(","):
        name, _, count = entry.rpartition(":")
        if name.split(":")[0] == "gpu" and count.isdigit():
            total += int(count)
    return total


def _tres_gpus(tres):
    """Read the GPU count from a TRES string.

    Parameters
    ----------
    tres : str
        ``tres-alloc`` value such as ``cpu=4,gres/gpu=2``.

    Returns
    -------
    int
        Allocated GPUs, 0 when there are none.
    """
    for entry in tres.split(","):
        key, _, value = entry.partition("=")
        if key == "gres/gpu" and value.isdigit():
            return int(value)
    return 0


def _number(text, kind):
    """Convert a Slurm number, mapping unknown values to None.

    Parameters
    ----------
    text : str
        Value printed by Slurm.
    kind : type
        ``int`` or ``float``.

    Returns
    -------
    int or float or None
        The value, or None for ``N/A`` and other non-numbers.
    """
    try:
        return kind(text)
    except ValueError:
        return None


def _seconds(text):
    """Convert a Slurm duration such as ``1-02:03:04`` to seconds.

    Parameters
    ----------
    text : str
        ``[D-]HH:MM:SS``, ``MM:SS`` or ``SS``.

    Returns
    -------
    int or None
        Seconds, or None when the value is not a duration.
    """
    days, _, clock = text.rpartition("-")
    try:
        seconds = 0
        for part in clock.split(":"):
            seconds = seconds * 60 + int(part)
        return seconds + int(days or 0) * 86400
    except ValueError:
        return None


@functools.cache
def _user_name(uid):
    """Resolve a user ID, falling back to the number.

    Parameters
    ----------
    uid : str
        Numeric user ID.

    Returns
    -------
    str
        User name.
    """
    try:
        return pwd.getpwuid(int(uid)).pw_name
    except (KeyError, ValueError):
        return uid


@functools.cache
def _group_name(gid):
    """Resolve a group ID, falling back to the number.

    Parameters
    ----------
    gid : str
        Numeric group ID.

    Returns
    -------
    str
        Group name.
    """
    try:
        return grp.getgrgid(int(gid)).gr_name
    except (KeyError, ValueError):
        return gid
