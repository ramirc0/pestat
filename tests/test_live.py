"""Checks against the live cluster. Run with ``pytest -m live``."""

import csv
import io
import json
import shutil
import subprocess
import sys

import pytest
from conftest import REFERENCE, REFERENCE_SKIP

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(shutil.which("sinfo") is None, reason="needs Slurm"),
]


def pestat(*args):
    result = subprocess.run(
        [sys.executable, "-m", "pestat", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_json_lists_every_node_once():
    nodes = json.loads(pestat("-o", "json"))["nodes"]
    names = [n["hostname"] for n in nodes]
    assert names and len(names) == len(set(names))


@pytest.mark.parametrize(("fmt", "delimiter"), [("tsv", "\t"), ("csv", ",")])
def test_tables_have_equal_field_counts(fmt, delimiter):
    rows = list(csv.reader(io.StringIO(pestat("-o", fmt)), delimiter=delimiter))
    assert len({len(row) for row in rows}) == 1


@pytest.mark.skipif(REFERENCE_SKIP, reason="needs bash and gawk")
def test_same_nodes_as_the_reference():
    ref = subprocess.run(
        ["bash", str(REFERENCE), "-c"], capture_output=True, text=True, check=False
    ).stdout.splitlines()
    start = next(i for i, line in enumerate(ref) if line.startswith("Hostname"))
    ref_names = [line.split()[0] for line in ref[start + 2 :]]
    names = [n["hostname"] for n in json.loads(pestat("-o", "json"))["nodes"]]
    assert names == ref_names


def test_unknown_partitions_warn():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pestat",
            "-o",
            "json",
            "-p",
            "pestat-no-such-partition",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout)["nodes"] == []
    assert "no nodes matched partition" in result.stderr
