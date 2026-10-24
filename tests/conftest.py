import grp
import json
import os
import pwd
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).parent
REFERENCE = TESTS / "reference" / "pestat"
UID, GID = os.getuid(), os.getgid()
USER = pwd.getpwuid(UID).pw_name
GROUP = grp.getgrgid(GID).gr_name

# The reference sources this file unconditionally and hardcodes gawk.
REFERENCE_SKIP = (
    Path("/etc/pestat.conf").exists()
    or shutil.which("bash") is None
    or "GNU Awk"
    not in subprocess.run(
        ["/usr/bin/awk", "--version"], capture_output=True, text=True, check=False
    ).stdout
)


def node(name, partition="long", **fields):
    row = {
        "NodeList": name,
        "Partition": partition,
        "CPUsState": "0/8/0/8",
        "CPUsLoad": "0.05",
        "Memory": "64000",
        "FreeMem": "60000",
        "StateCompact": "idle",
        "Threads": "1",
        "Gres": "(null)",
        "GresUsed": "gpu:0",
        "Reason": "none",
    }
    row.update(fields)
    return row


def job(job_id, nodelist, **fields):
    row = {
        "JobID": str(job_id),
        "JobArrayID": str(job_id),
        "UserID": UID,
        "GroupID": GID,
        "Account": "a1",
        "QOS": "normal",
        "Reservation": "(null)",
        "StartTime": "2026-10-24T08:00:00",
        "EndTime": "2026-10-25T08:00:00",
        "TimeUsed": "1:00:00",
        "Partition": "long",
        "NumNodes": "1",
        "tres-alloc": "cpu=4,mem=1000M,node=1,billing=4",
        "NodeList": nodelist,
        "Name": f"job{job_id}",
        "State": "RUNNING",
    }
    row.update(fields)
    return row


def clean_cluster():
    """Return a snapshot on which the reference and pestat agree."""
    nodes = [
        node("n01", "short*"),
        node("n01", "long"),
        node(
            "n02", "short*", CPUsState="8/0/0/8", CPUsLoad="8.10", StateCompact="alloc"
        ),
        node("n02", "long", CPUsState="8/0/0/8", CPUsLoad="8.10", StateCompact="alloc"),
        node("n03", CPUsState="4/4/0/8", CPUsLoad="9.50", StateCompact="mix"),
        node("n04", CPUsState="8/0/0/8", CPUsLoad="5.50", StateCompact="alloc"),
        node("n05", FreeMem="5000"),
        node("n06", FreeMem="10000"),
        node("n07", "short*", StateCompact="drain"),
        node(
            "n08",
            "gpu",
            CPUsState="16/16/0/32",
            CPUsLoad="3.00",
            StateCompact="mix",
            Threads="2",
            Gres="gpu:v100:4",
            GresUsed="gpu:v100:2(IDX:0-1)",
        ),
        node(
            "n09",
            "gpu",
            CPUsState="0/32/0/32",
            Threads="2",
            Gres="gpu:v100:4",
            GresUsed="gpu:v100:0(IDX:N/A)",
        ),
        node("n10", CPUsState="2/6/0/8", CPUsLoad="2.00", StateCompact="mix"),
        node("n11", CPUsState="4/4/0/8", CPUsLoad="12.00", StateCompact="mix"),
        node("n12", CPUsState="4/4/0/8", CPUsLoad="12.00", StateCompact="mix"),
        node("n13", "secret", hidden=True),
    ]
    jobs = [
        job(101, "n02", Partition="short", EndTime="2026-10-24T20:00:00"),
        job(
            102, "n03", UserID=0, GroupID=0, Account="a2", QOS="high", Reservation="r1"
        ),
        job(
            103,
            "n08",
            Partition="gpu",
            **{"tres-alloc": "cpu=16,mem=1000M,node=1,gres/gpu=2,gres/gpu:v100=2"},
        ),
        job(104, "n10", TimeUsed="2-03:04:05"),
        job(105, "n10", UserID=0, GroupID=0, EndTime="2026-10-26T08:00:00"),
        job(106, "n10", TimeUsed="20:00"),
        job(
            110,
            "n[11-12]",
            NumNodes="2",
            **{"tres-alloc": "cpu=8,mem=2000M,node=2,billing=8"},
        ),
        job(120, "(null)", State="PENDING"),
    ]
    return {
        "nodes": nodes,
        "jobs": jobs,
        "accounts": ["a1", "a2"],
        "qos": ["normal", "high"],
        "users": [USER, "root"],
    }


class FakeCluster:
    """A snapshot plus fake Slurm commands that serve it."""

    def __init__(self, root):
        self.root = root
        self.bin = root / "bin"
        self.home = root / "home"
        self.cwd = root / "cwd"
        self.state = root / "state.json"
        for path in (self.bin, self.home, self.cwd):
            path.mkdir()
        for command in ("sinfo", "squeue", "scontrol", "sacctmgr"):
            script = self.bin / command
            script.write_text(
                f'#!/bin/sh\nexec {sys.executable} {TESTS / "fakeslurm.py"} {self.state} {command} "$@"\n'
            )
            script.chmod(0o755)
        (self.home / ".pestat.conf").write_text(f"prefix={self.bin}\n")
        self.write(clean_cluster())

    def write(self, snapshot):
        self.state.write_text(json.dumps(snapshot))
        self.calls_path.unlink(missing_ok=True)

    @property
    def calls_path(self):
        return self.root / "calls.jsonl"

    def calls(self):
        if not self.calls_path.exists():
            return []
        return [json.loads(line) for line in self.calls_path.read_text().splitlines()]

    def _run(self, argv, env=None):
        base = {
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "HOME": str(self.home),
            "LC_ALL": "C.UTF-8",
        }
        return subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            cwd=self.cwd,
            env=base | (env or {}),
        )

    def reference(self, *args, env=None):
        return self._run(["bash", str(REFERENCE), *args], env)

    def pestat(self, *args, env=None):
        return self._run([sys.executable, "-m", "pestat", *args], env)


@pytest.fixture
def cluster(tmp_path):
    return FakeCluster(tmp_path)


def parse_reference(stdout):
    """Read the reference's no-color rows into comparable records."""
    lines = stdout.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("Hostname"))
    gres = "GRES/" in lines[start]
    rows = []
    for line in lines[start + 2 :]:
        tokens = line.split()
        rest = " ".join(tokens[9 if gres else 8 :])
        jobs_flag = rest.endswith("*")
        entries = [e.strip() for e in rest.rstrip("*").split("|")]
        rows.append(
            {
                "hostname": tokens[0],
                "partition": tokens[1],
                "flags": {
                    "state": tokens[2].endswith("*"),
                    "load": tokens[5].endswith("*"),
                    "memory": tokens[7].endswith("*"),
                    "jobs": jobs_flag,
                },
                "gpus": tokens[8] if gres else None,
                "job_ids": [
                    e.split()[0] for e in entries if e and not e.startswith("LASTJOB=")
                ],
            }
        )
    return rows


def pytest_addoption(parser):
    parser.addoption(
        "--update-golden", action="store_true", help="Rewrite tests/golden snapshots."
    )


@pytest.fixture
def golden(request):
    """Compare text with ``tests/golden/<name>.txt``, writing it when asked."""

    def check(name, text):
        path = TESTS / "golden" / f"{name}.txt"
        if request.config.getoption("--update-golden") or not path.exists():
            path.parent.mkdir(exist_ok=True)
            path.write_text(text)
            if not request.config.getoption("--update-golden"):
                pytest.fail(f"wrote missing snapshot {path.name}; review and rerun")
        assert text == path.read_text()

    return check
