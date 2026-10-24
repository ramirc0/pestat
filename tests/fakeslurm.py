"""Emulate sinfo, squeue, scontrol and sacctmgr from a JSON cluster snapshot.

Usage: ``fakeslurm.py STATE.json COMMAND ARGS...``. The snapshot holds
``nodes`` (one row per node and partition, keyed by sinfo ``-O`` field
names), ``jobs`` (keyed by squeue ``-O`` field names), ``accounts``, ``qos``
and ``users``. Every call is appended to ``calls.jsonl`` beside the snapshot.
"""

import grp
import json
import pwd
import re
import sys
from pathlib import Path

from pestat.hostlist import expand

STATES = {
    "alloc": "alloc",
    "allocated": "alloc",
    "comp": "comp",
    "completing": "comp",
    "down": "down",
    "drain": "drain",
    "drained": "drain",
    "draining": "drng",
    "drng": "drng",
    "fail": "fail",
    "idle": "idle",
    "inval": "inval",
    "maint": "maint",
    "mix": "mix",
    "mixed": "mix",
    "powered_down": "~",
    "resv": "resv",
}


def base_state(state):
    return state.rstrip("*~#!%$@^-")


def user_name(uid):
    try:
        return pwd.getpwuid(int(uid)).pw_name
    except KeyError:
        return str(uid)


def group_name(gid):
    try:
        return grp.getgrgid(int(gid)).gr_name
    except KeyError:
        return str(gid)


def value(row, field):
    key = field.lower()
    if key == "username":
        return user_name(row["UserID"])
    if key == "groupname":
        return group_name(row["GroupID"])
    for name, val in row.items():
        if name.lower() == key:
            return str(val)
    raise SystemExit(f"fakeslurm: unknown field {field}")


def parse_spec(spec):
    fields = []
    for item in spec.split(","):
        name, colon, rest = item.partition(":")
        if not colon:
            fields.append((name, 20, False, ""))
            continue
        match = re.match(r"(\.?)(\d*)(.*)", rest)
        right, width, suffix = match.groups()
        fields.append((name, int(width or 0), bool(right), suffix))
    return fields


def render(rows, spec, header):
    fields = parse_spec(spec)
    lines = []
    if header:
        lines.append(cells({}, fields, header=True))
    lines.extend(cells(row, fields) for row in rows)
    return "".join(line + "\n" for line in lines)


def cells(row, fields, header=False):
    out = []
    for name, width, right, suffix in fields:
        text = name.upper() if header else value(row, name)
        if width:
            text = text[:width]
            text = text.rjust(width) if right else text.ljust(width)
        out.append(text + suffix)
    return "".join(out)


def options(args, flags=()):
    opts, i = {}, 0
    while i < len(args):
        arg = args[i]
        if arg in flags:
            opts[arg] = True
            i += 1
        elif "=" in arg and arg.startswith("--"):
            key, _, val = arg.partition("=")
            opts[key] = val
            i += 1
        else:
            opts[arg] = args[i + 1]
            i += 2
    return opts


def sinfo(state, args):
    opts = options(args, flags=("-h", "-N", "--all", "-a"))
    rows = state["nodes"]
    partitions = opts.get("-p")
    if partitions is not None:
        wanted = partitions.split(",")
        rows = [r for r in rows if r["Partition"].rstrip("*") in wanted]
    elif not ("--all" in opts or "-a" in opts):
        rows = [r for r in rows if not r.get("hidden")]
    if "-n" in opts:
        try:
            hosts = set(expand(opts["-n"]))
        except ValueError:
            print(f"sinfo: error: Invalid node name {opts['-n']}", file=sys.stderr)
            return 1
        rows = [r for r in rows if r["NodeList"] in hosts]
    if "--states" in opts:
        wanted = []
        for name in opts["--states"].lower().split(","):
            if name not in STATES:
                print(f"sinfo: error: Invalid node state: {name}", file=sys.stderr)
                return 1
            wanted.append(STATES[name])
        rows = [r for r in rows if matches_state(r["StateCompact"], wanted)]
    if "-O" in opts:
        sys.stdout.write(render(rows, opts["-O"], header="-h" not in opts))
    return 0


def matches_state(state, wanted):
    return base_state(state) in wanted or ("~" in wanted and "~" in state)


def squeue(state, args):
    opts = options(args, flags=("-h", "--all", "-a"))
    hidden = {r["Partition"].rstrip("*") for r in state["nodes"] if r.get("hidden")}
    jobs = state["jobs"]
    if "-j" in opts:
        ids = opts["-j"].split(",")
        if not all(re.fullmatch(r"\d+(_\d+)?", i) for i in ids):
            print(f"squeue: error: Invalid job id: {opts['-j']}", file=sys.stderr)
            return 1
        jobs = [j for j in jobs if job_matches(j, ids)]
        if len(ids) == 1 and not jobs:
            print("slurm_load_jobs error: Invalid job id specified", file=sys.stderr)
            return 1
    if "-t" in opts:
        wanted = {
            "R" if s.upper() in ("R", "RUNNING") else s.upper()
            for s in opts["-t"].split(",")
        }
        jobs = [
            j
            for j in jobs
            if ("R" if j.get("State", "RUNNING") == "RUNNING" else j["State"]) in wanted
        ]
    if not ("--all" in opts or "-a" in opts):
        jobs = [j for j in jobs if j["Partition"] not in hidden]
    for flag, field in (("-A", "Account"), ("-q", "QOS"), ("-R", "Reservation")):
        if flag in opts:
            jobs = [j for j in jobs if j[field] in opts[flag].split(",")]
    if "-O" in opts:
        sys.stdout.write(render(jobs, opts["-O"], header="-h" not in opts))
    return 0


def job_matches(job, ids):
    array_id = job["JobArrayID"]
    return any(i in (job["JobID"], array_id, array_id.split("_")[0]) for i in ids)


def scontrol(state, args):
    if args[:2] != ["show", "hostnames"]:
        raise SystemExit(f"fakeslurm: unsupported scontrol {args}")
    sys.stdout.write("".join(host + "\n" for host in expand(args[2])))
    return 0


def sacctmgr(state, args):
    words = [a for a in args if a not in ("-p", "-n")]
    kind, names = words[1], words[-1].removeprefix("users=")
    known = {"assoc": "users", "account": "accounts", "qos": "qos"}[kind]
    if set(names.split(",")) & set(state.get(known, [])):
        print(f"{names}|")
    return 0


def main():
    path, command, args = Path(sys.argv[1]), sys.argv[2], sys.argv[3:]
    with open(path.with_name("calls.jsonl"), "a") as log:
        log.write(json.dumps([command, *args]) + "\n")
    state = json.loads(path.read_text())
    handlers = {
        "sinfo": sinfo,
        "squeue": squeue,
        "scontrol": scontrol,
        "sacctmgr": sacctmgr,
    }
    return handlers[command](state, args)


if __name__ == "__main__":
    sys.exit(main())
