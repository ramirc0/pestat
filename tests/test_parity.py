"""Compare pestat's selections and flags with the upstream script's."""

import json

import pytest
from conftest import GROUP, REFERENCE_SKIP, USER, parse_reference

pytestmark = [
    pytest.mark.reference,
    pytest.mark.skipif(
        REFERENCE_SKIP, reason="needs bash, gawk and no /etc/pestat.conf"
    ),
]

CASES = {
    "default": [],
    "per-partition": ["-2"],
    "gres": ["-G"],
    "details": ["-G", "-N", "-S", "-E", "-T", "-a"],
    "flagged": ["-f"],
    "critical": ["-F"],
    "omit-down": ["-d"],
    "all-partitions": ["-P"],
    "one-partition": ["-p", "long"],
    "partition-list": ["-p", "short,gpu"],
    "nodes": ["-n", "n03,n10"],
    "states": ["-t", "mix"],
    "user": ["-u", USER],
    "root": ["-u", "root"],
    "group": ["-g", GROUP],
    "account": ["-A", "a2"],
    "qos": ["-q", "high"],
    "reservation": ["-R", "r1"],
    "job": ["-j", "102"],
    "multinode-job": ["-j", "110"],
}


def python_rows(result):
    return json.loads(result.stdout)["nodes"]


@pytest.mark.parametrize("args", CASES.values(), ids=list(CASES))
def test_same_nodes_flags_and_jobs(cluster, args):
    ref = cluster.reference("-c", *args)
    py = cluster.pestat("-o", "json", *args)
    assert (py.returncode, ref.returncode) == (0, 0), py.stderr
    expected = {}
    for row in parse_reference(ref.stdout):
        expected.setdefault(row["hostname"], row)
    got = python_rows(py)
    assert [n["hostname"] for n in got] == list(expected)
    for node in got:
        row = expected[node["hostname"]]
        assert {k: v is not None for k, v in node["flags"].items()} == row["flags"]
        assert [j["array_id"] or j["job_id"] for j in node["jobs"]] == row["job_ids"]


@pytest.mark.parametrize(
    "args", [["-m", "20000"], ["-M", "20000"]], ids=["less", "more"]
)
def test_same_nodes_for_free_memory_filters(cluster, args):
    ref = parse_reference(cluster.reference("-c", *args).stdout)
    py = python_rows(cluster.pestat("-o", "json", *args))
    assert [n["hostname"] for n in py] == [r["hostname"] for r in ref]


@pytest.mark.parametrize(("args", "count"), [([], 2), (["-j", "110"], 3)])
def test_python_runs_only_sinfo_and_squeue(cluster, args, count):
    cluster.pestat(*args)
    calls = cluster.calls()
    assert len(calls) == count
    assert {call[0] for call in calls} <= {"sinfo", "squeue"}
