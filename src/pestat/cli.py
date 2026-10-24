"""Command-line interface for pestat."""

import argparse
import grp
import os
import pwd
import signal
import sys

from pestat import output, slurm, status
from pestat.hostlist import expand

CONFIG_FILES = ("/etc/pestat.conf", "~/.pestat.conf")
STATEDOWN = "down down~ idle~ alloc# drain drng resv maint boot"


def _names(text):
    """Split a comma-separated option value.

    Parameters
    ----------
    text : str
        Value such as ``gpu,gpuh200``.

    Returns
    -------
    list of str
        Non-empty names in order.
    """
    return [name for name in text.split(",") if name]


def _positive(text):
    """Parse a positive integer option value.

    Parameters
    ----------
    text : str
        Option value.

    Returns
    -------
    int
        The value.

    Raises
    ------
    argparse.ArgumentTypeError
        If the value is not an integer above 0.
    """
    try:
        value = int(text)
    except ValueError:
        value = 0
    if value <= 0:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got {text!r}")
    return value


def _switch(text):
    """Read a config on/off value such as ``1`` or ``0``.

    Parameters
    ----------
    text : str
        Config value.

    Returns
    -------
    bool
        True unless the value is ``0``, ``no``, ``false``, ``off`` or empty.
    """
    return text.strip().lower() not in ("", "0", "no", "false", "off")


def _optional(text):
    """Read a config number where 0 or less means unset.

    Parameters
    ----------
    text : str
        Config value.

    Returns
    -------
    int or None
        The value, or None when it is not positive.
    """
    value = int(text)
    return value if value > 0 else None


CONFIG_TYPES = {
    "colors": _switch,
    "cpuload_delta1": float,
    "cpuload_delta2": float,
    "flaglevel": _optional,
    "free_mem_less": _optional,
    "free_mem_more": _optional,
    "groupname": _names,
    "hostlist": lambda v: v.removeprefix("-n").strip(),
    "jobgracetime": int,
    "memory_thres1": float,
    "memory_thres2": float,
    "omitdown": _switch,
    "partition": lambda v: _names(v.removeprefix("-p").strip()),
    "prefix": str,
    "printaccount": _switch,
    "printgres": _switch,
    "printjobendtime": _switch,
    "printjobname": _switch,
    "printjobstarttime": _switch,
    "printtimeused": _switch,
    "statedown": str.split,
    "uniquenodes": _switch,
    "username": _names,
}


def build_parser():
    """Build the ``pestat`` argument parser.

    Short flags match upstream pestat. Option destinations use the
    variable names of ``pestat.conf``, so config values become defaults.

    Returns
    -------
    argparse.ArgumentParser
        Parser for the pestat flags.

    Examples
    --------
    >>> opts = build_parser().parse_args(["-p", "gpu,gpuh200,foobar", "-2", "-o", "json"])
    >>> opts.partition, opts.uniquenodes, opts.format
    (['gpu', 'gpuh200', 'foobar'], False, 'json')
    """
    p = argparse.ArgumentParser(
        prog="pestat",
        description="Print Slurm node status with one line per node and its running jobs.",
        epilog="Settings are read from /etc/pestat.conf, then ~/.pestat.conf.",
    )
    p.add_argument(
        "-p",
        "--partition",
        type=_names,
        help="Only nodes in these comma-separated partitions.",
    )
    p.add_argument(
        "-P",
        "--all-partitions",
        action="store_true",
        help="Include hidden and unavailable partitions.",
    )
    p.add_argument(
        "-u",
        "--user",
        dest="username",
        type=_names,
        help="Only nodes running jobs of these users.",
    )
    p.add_argument(
        "-g",
        "--group",
        dest="groupname",
        type=_names,
        help="Only jobs of these UNIX groups, and their nodes.",
    )
    p.add_argument(
        "-A",
        "--account",
        dest="accountlist",
        help="Only jobs in these Slurm accounts, and their nodes.",
    )
    p.add_argument(
        "-a",
        "--print-account",
        dest="printaccount",
        action="store_true",
        help="Print user(account) for each job.",
    )
    p.add_argument(
        "-q", "--qos", dest="qoslist", help="Only jobs with these QOS, and their nodes."
    )
    p.add_argument(
        "-R",
        "--reservation",
        dest="reservationlist",
        help="Only jobs in these reservations, and their nodes.",
    )
    p.add_argument(
        "-s", "-t", "--states", dest="statelist", help="Only nodes in these states."
    )
    p.add_argument(
        "-n", "-w", "--nodes", dest="hostlist", help="Only nodes in this hostlist."
    )
    p.add_argument(
        "-j", "--jobs", dest="joblist", help="Only nodes running these jobs."
    )
    p.add_argument(
        "-G",
        "--gres",
        dest="printgres",
        action="store_true",
        help="Print the GPUs of each job.",
    )
    p.add_argument(
        "-N",
        "--job-name",
        dest="printjobname",
        action="store_true",
        help="Print the name of each job.",
    )
    listing = p.add_mutually_exclusive_group()
    listing.add_argument(
        "-f",
        "--flagged",
        dest="flaglevel",
        action="store_const",
        const=1,
        help="Only flagged nodes (! and *).",
    )
    listing.add_argument(
        "-F",
        "--critical",
        dest="flaglevel",
        action="store_const",
        const=2,
        help="Only nodes with a critical flag (!).",
    )
    listing.add_argument(
        "-m",
        "--free-mem-less",
        dest="free_mem_less",
        type=_positive,
        metavar="MB",
        help="Only nodes with less free memory.",
    )
    listing.add_argument(
        "-M",
        "--free-mem-more",
        dest="free_mem_more",
        type=_positive,
        metavar="MB",
        help="Only nodes with more free memory.",
    )
    p.add_argument(
        "-d",
        "--omit-down",
        dest="omitdown",
        action="store_true",
        help=f"Omit nodes in states: {STATEDOWN}.",
    )
    p.add_argument(
        "-1",
        "--unique",
        dest="uniquenodes",
        action="store_true",
        help="One line per node (default).",
    )
    p.add_argument(
        "-2",
        "--per-partition",
        dest="uniquenodes",
        action="store_false",
        help="One line per node and partition.",
    )
    p.add_argument(
        "-S",
        "--start-time",
        dest="printjobstarttime",
        action="store_true",
        help="Print the start time of each job.",
    )
    p.add_argument(
        "-E",
        "--end-time",
        dest="printjobendtime",
        action="store_true",
        help="Print the end time of each job.",
    )
    p.add_argument(
        "-T",
        "--time-used",
        dest="printtimeused",
        action="store_true",
        help="Print the time used by each job.",
    )
    p.add_argument(
        "-C",
        "--color",
        dest="colors",
        action="store_const",
        const=True,
        help="Force colors on.",
    )
    p.add_argument(
        "-c",
        "--no-color",
        dest="colors",
        action="store_const",
        const=False,
        help="Force colors off.",
    )
    p.add_argument(
        "-o",
        "--format",
        choices=("text", "json", "tsv", "csv"),
        default="text",
        help="Output format.",
    )
    p.add_argument(
        "-V", "--version", action="store_true", help="Print the version and exit."
    )
    p.set_defaults(
        uniquenodes=True,
        cpuload_delta1=4.0,
        cpuload_delta2=2.0,
        memory_thres1=0.1,
        memory_thres2=0.2,
        jobgracetime=900,
        statedown=STATEDOWN.split(),
        prefix="",
    )
    return p


def read_config(path):
    r"""Read ``NAME=value`` settings from a pestat config file.

    Upstream pestat sources these files as bash. Here only assignments
    are read, with optional ``export``, quotes, comments and ``$VAR``
    expansion. No shell runs. Lines that are not assignments, unknown
    names and bad values print a warning and are skipped.

    Parameters
    ----------
    path : str
        Config file. ``~`` expands. A missing file gives no settings.

    Returns
    -------
    dict
        Settings converted to option values.

    Examples
    --------
    >>> import tempfile
    >>> with tempfile.NamedTemporaryFile("w", suffix=".conf") as f:
    ...     _ = f.write('export colors=0  # off\npartition="-p gpu,long"\n')
    ...     f.flush()
    ...     read_config(f.name)
    {'colors': False, 'partition': ['gpu', 'long']}
    """
    try:
        with open(os.path.expanduser(path)) as file:
            text = file.read()
    except FileNotFoundError:
        return {}
    except OSError as error:
        _warn(f"cannot read {path}: {error.strerror}")
        return {}
    import shlex

    settings = {}
    for number, line in enumerate(text.splitlines(), 1):
        where = f"{path}:{number}"
        try:
            words = shlex.split(line, comments=True)
        except ValueError as error:
            _warn(f"{where}: {error}, line ignored")
            continue
        if words[:1] == ["export"]:
            words = words[1:]
        if not words:
            continue
        name, equals, value = words[0].partition("=")
        if len(words) > 1 or not equals:
            _warn(f"{where}: expected NAME=value, line ignored")
        elif name not in CONFIG_TYPES:
            _warn(f"{where}: unknown setting {name!r} ignored")
        else:
            try:
                settings[name] = CONFIG_TYPES[name](os.path.expandvars(value))
            except ValueError:
                _warn(f"{where}: bad value {value!r} for {name} ignored")
    return settings


def main(argv=None):
    """Run pestat.

    Parameters
    ----------
    argv : list of str, optional
        Arguments without the program name. None reads ``sys.argv``.

    Returns
    -------
    int
        Exit status: 0 on success, 1 on a runtime error. Usage errors exit
        with status 2.

    Examples
    --------
    >>> main(["-V"])  # doctest: +ELLIPSIS
    pestat ...
    0
    """
    # Exit quietly when a pager or `head` closes the pipe.
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    parser = build_parser()
    for path in CONFIG_FILES:
        parser.set_defaults(**read_config(path))
    opts = parser.parse_args(argv)
    if opts.version:
        # importlib.metadata costs about 25 ms, so it loads only here.
        from importlib.metadata import version

        print(f"pestat {version('pestat')}")
        return 0
    try:
        return _report(opts)
    except _Error as error:
        print(f"pestat: error: {error}", file=sys.stderr)
        return 1


class _Error(Exception):
    """A runtime error reported as one line on stderr."""


def _report(opts):
    """Query Slurm, then select, flag and write the nodes.

    Parameters
    ----------
    opts : argparse.Namespace
        Parsed options.

    Returns
    -------
    int
        Exit status 0.

    Raises
    ------
    _Error
        If a name is unknown or a Slurm command fails.
    """
    for names, lookup, kind in (
        (opts.username, pwd.getpwnam, "user"),
        (opts.groupname, grp.getgrnam, "group"),
    ):
        for name in names or ():
            try:
                lookup(name)
            except KeyError:
                raise _Error(f"unknown {kind} {name!r}") from None
    commands = [
        slurm.sinfo_command(
            partitions=opts.partition or (),
            nodes=opts.hostlist,
            states=opts.statelist,
            all_partitions=opts.all_partitions,
            prefix=opts.prefix,
        ),
        slurm.squeue_command(
            accounts=opts.accountlist,
            qos=opts.qoslist,
            reservations=opts.reservationlist,
            prefix=opts.prefix,
        ),
    ]
    if opts.joblist:
        commands.append(slurm.job_nodes_command(opts.joblist, prefix=opts.prefix))
    try:
        sinfo, squeue, *jobs = slurm.run(commands)
    except OSError as error:
        raise _Error(f"cannot run {error.filename}: {error.strerror}") from None
    for result in (sinfo, squeue):
        if result.returncode:
            detail = result.stderr.strip() or f"exit status {result.returncode}"
            raise _Error(f"{os.path.basename(result.args[0])}: {detail}")
    if jobs and (jobs[0].returncode or not jobs[0].stdout.strip()):
        raise _Error(f"jobs {opts.joblist} are not running")

    nodes, default = slurm.parse_nodes(sinfo.stdout.splitlines())
    seen = {p for node in nodes.values() for p in node["partitions"]}
    for partition in opts.partition or ():
        if partition not in seen:
            _warn(f"no nodes matched partition {partition!r}")
    if jobs:
        hosts = {
            host for nodelist in jobs[0].stdout.split() for host in expand(nodelist)
        }
        nodes = {host: node for host, node in nodes.items() if host in hosts}
    job_rows = slurm.parse_jobs(squeue.stdout.splitlines())
    selection = [
        ("account", opts.accountlist),
        ("QOS", opts.qoslist),
        ("reservation", opts.reservationlist),
    ]
    if not job_rows:
        for kind, value in selection:
            if value:
                _warn(f"no running jobs matched {kind} {value!r}")
    rows = status.build(nodes, job_rows, opts)

    if opts.format == "json":
        output.write_json(rows, sys.stdout, default_partition=default)
    elif opts.format in ("tsv", "csv"):
        output.write_table(rows, sys.stdout, "\t" if opts.format == "tsv" else ",")
    else:
        tty = sys.stdout.isatty()
        colors = (
            opts.colors
            if opts.colors is not None
            else tty and "NO_COLOR" not in os.environ
        )
        output.write_text(
            rows,
            opts,
            sys.stdout,
            default_partition=default,
            colors=colors,
            width=_terminal_width() if tty else None,
        )
    return 0


def _terminal_width():
    """Return the terminal width from ``COLUMNS`` or the terminal itself.

    Returns
    -------
    int or None
        Columns, or None when unknown.
    """
    try:
        return int(os.environ["COLUMNS"])
    except (KeyError, ValueError):
        pass
    try:
        return os.get_terminal_size(sys.stdout.fileno()).columns
    except OSError:
        return None


def _warn(message):
    """Print a warning on stderr.

    Parameters
    ----------
    message : str
        Warning text.
    """
    print(f"pestat: warning: {message}", file=sys.stderr)
