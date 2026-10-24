import pytest
from conftest import REFERENCE_SKIP, parse_reference

pytestmark = [
    pytest.mark.reference,
    pytest.mark.skipif(
        REFERENCE_SKIP, reason="needs bash, gawk and no /etc/pestat.conf"
    ),
]


def test_reference_runs_on_the_fake_cluster(cluster):
    result = cluster.reference()
    assert result.returncode == 0, result.stderr
    rows = parse_reference(result.stdout)
    assert [r["hostname"] for r in rows] == [f"n{i:02d}" for i in range(1, 13)]
    flagged = {r["hostname"]: [k for k, v in r["flags"].items() if v] for r in rows}
    assert flagged["n03"] == ["load"]
    assert flagged["n05"] == ["memory"]
    assert flagged["n07"] == ["state"]
    assert flagged["n10"] == ["jobs"]
    assert flagged["n11"] == ["load", "jobs"]


def test_reference_expands_multinode_jobs_with_scontrol(cluster):
    cluster.reference()
    assert ["scontrol", "show", "hostnames", "n[11-12]"] in cluster.calls()


def test_reference_gres_column_reads_typed_gres(cluster):
    rows = parse_reference(cluster.reference("-G").stdout)
    assert {r["hostname"]: r["gpus"] for r in rows}["n08"] == "4/2"
