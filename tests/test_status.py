import pytest

from pestat.cli import build_parser
from pestat.status import build, flags

OPTS = build_parser().parse_args([])


def make_node(**fields):
    node = {
        "hostname": "n1",
        "partitions": ["long"],
        "state": "alloc",
        "reason": None,
        "cpus_alloc": 8,
        "cpus_total": 16,
        "cpu_load": 8.0,
        "threads_per_core": 1,
        "memory_mb": 1000,
        "free_mem_mb": 500,
        "gpus_total": 0,
        "gpus_used": 0,
        "flags": {},
        "jobs": [],
    }
    node.update(fields)
    return node


def make_job(**fields):
    job = {
        "job_id": "1",
        "array_id": None,
        "user": "alice",
        "group": "lab",
        "num_nodes": 1,
        "time_used_s": 3600,
        "nodelist": "n1",
    }
    job.update(fields)
    return job


@pytest.mark.parametrize(
    ("load", "level"),
    [
        (12.0, "magenta"),
        (12.01, "red"),
        (10.0, None),
        (10.01, "magenta"),
        (6.0, None),
        (5.99, "magenta"),
        (4.0, "magenta"),
        (3.99, "red"),
    ],
)
def test_load_bounds_are_strict(load, level):
    assert flags(make_node(cpu_load=load), OPTS)["load"] == level


def test_threads_lower_the_minimum_load():
    node = make_node(cpu_load=3.0, threads_per_core=2)
    assert flags(node, OPTS)["load"] is None


def test_gpus_in_use_set_the_load_bounds():
    node = make_node(
        cpus_alloc=32, cpus_total=32, cpu_load=3.0, gpus_total=4, gpus_used=2
    )
    assert flags(node, OPTS)["load"] is None
    assert flags({**node, "cpu_load": 20.01}, OPTS)["load"] == "red"


@pytest.mark.parametrize(
    ("free", "level"), [(100, "magenta"), (99, "red"), (200, None), (199, "magenta")]
)
def test_memory_thresholds_are_strict(free, level):
    assert flags(make_node(free_mem_mb=free), OPTS)["memory"] == level


@pytest.mark.parametrize(
    ("state", "level"),
    [
        ("alloc", None),
        ("idle", None),
        ("mix-", None),
        ("drain", "red"),
        ("drng", "red"),
        ("down*", "red"),
        ("idle*", "red"),
        ("drain$", "red"),
        ("drain@", "red"),
        ("inval", "red"),
    ],
)
def test_state_flags_use_the_base_state(state, level):
    assert flags(make_node(state=state), OPTS)["state"] == level


def test_more_jobs_than_cpus_is_critical():
    jobs = [make_job(job_id=str(i)) for i in range(3)]
    assert flags(make_node(cpus_alloc=2, jobs=jobs), OPTS)["jobs"] == "red"


def test_grace_period_starts_with_the_newest_job():
    young = make_job(time_used_s=899)
    old = make_job(time_used_s=3600)
    node = make_node(cpu_load=0.0, jobs=[old, young])
    assert flags(node, OPTS)["load"] is None
    assert flags({**node, "jobs": [old]}, OPTS)["load"] == "red"


def test_group_filter_drops_other_jobs():
    opts = build_parser().parse_args(["-g", "lab"])
    nodes = {
        "n1": make_node(),
        "n2": make_node(hostname="n2"),
    }
    jobs = [make_job(), make_job(job_id="2", group="other", nodelist="n[1-2]")]
    rows = build(nodes, jobs, opts)
    assert [n["hostname"] for n in rows] == ["n1"]
    assert [j["job_id"] for j in rows[0]["jobs"]] == ["1"]


def test_user_filter_keeps_every_job_on_the_node():
    opts = build_parser().parse_args(["-u", "bob"])
    nodes = {"n1": make_node(), "n2": make_node(hostname="n2")}
    jobs = [make_job(), make_job(job_id="2", user="bob")]
    rows = build(nodes, jobs, opts)
    assert [j["job_id"] for j in rows[0]["jobs"]] == ["1", "2"]
    assert len(rows) == 1


def test_omit_down_matches_substrings():
    opts = build_parser().parse_args(["-d"])
    nodes = {
        "n1": make_node(state="down*"),
        "n2": make_node(hostname="n2", state="mix"),
    }
    assert [n["hostname"] for n in build(nodes, [], opts)] == ["n2"]


def test_powered_down_nodes_are_hidden_unless_asked_for():
    nodes = {"n1": make_node(state="idle~")}
    assert build(nodes, [], OPTS) == []
    opts = build_parser().parse_args(["-t", "powered_down"])
    assert len(build({"n1": make_node(state="idle~")}, [], opts)) == 1
