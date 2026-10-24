"""Each test shows a bug or quirk of upstream pestat, then pestat's behavior."""

import json
import os
import subprocess
import sys

import pytest
from conftest import REFERENCE_SKIP, USER, clean_cluster, job, node, parse_reference

pytestmark = [
    pytest.mark.reference,
    pytest.mark.skipif(
        REFERENCE_SKIP, reason="needs bash, gawk and no /etc/pestat.conf"
    ),
]


def snapshot(nodes, jobs=(), **extra):
    base = clean_cluster()
    return {**base, "nodes": nodes, "jobs": list(jobs), **extra}


def nodes_of(result):
    assert result.returncode == 0, result.stderr
    return {n["hostname"]: n for n in json.loads(result.stdout)["nodes"]}


def ref_rows(result):
    return {r["hostname"]: r for r in parse_reference(result.stdout)}


def test_untyped_gpu_gres_counts(cluster):
    cluster.write(
        snapshot(
            [node("g1", "gpu", CPUsState="32/0/0/32", Gres="gpu:4", GresUsed="gpu:4")],
            [job(1, "g1", Partition="gpu")],
        )
    )
    assert ref_rows(cluster.reference("-G"))["g1"]["gpus"] == "0/0"
    g1 = nodes_of(cluster.pestat("-o", "json"))["g1"]
    assert (g1["gpus_used"], g1["gpus_total"]) == (4, 4)


def test_untyped_gpu_gres_with_suffixes_does_not_crash(cluster):
    cluster.write(
        snapshot(
            [
                node(
                    "g1",
                    "gpu",
                    CPUsState="16/16/0/32",
                    StateCompact="mix",
                    Gres="gpu:4(S:0-1)",
                    GresUsed="gpu:2(IDX:0-1)",
                )
            ],
            [job(1, "g1", Partition="gpu")],
        )
    )
    ref = cluster.reference()
    assert "division by zero" in ref.stderr
    assert nodes_of(cluster.pestat("-o", "json"))["g1"]["gpus_used"] == 2


def test_gpu_count_does_not_leak_into_the_next_node(cluster):
    cluster.write(
        snapshot(
            [
                node("g1", "gpu", Gres="gpu:a100:8", GresUsed="gpu:a100:0(IDX:N/A)"),
                node("c1", Gres="(null)", GresUsed="(null)"),
            ]
        )
    )
    assert ref_rows(cluster.reference("-G"))["c1"]["gpus"] == "8/0"
    assert nodes_of(cluster.pestat("-o", "json"))["c1"]["gpus_total"] == 0


def test_job_names_keep_spaces(cluster):
    cluster.write(snapshot([node("n1")], [job(1, "n1", Name="my long job")]))
    assert "my long job" not in cluster.reference("-N").stdout
    assert (
        nodes_of(cluster.pestat("-o", "json"))["n1"]["jobs"][0]["name"] == "my long job"
    )
    assert "1(my long job)" in cluster.pestat("-N").stdout


def test_user_lists_select_every_user(cluster):
    ref = ref_rows(cluster.reference("-u", f"root,{USER}"))
    assert set(ref) == {"n03", "n10"}
    assert set(nodes_of(cluster.pestat("-o", "json", "-u", f"root,{USER}"))) == {
        "n02",
        "n03",
        "n08",
        "n10",
        "n11",
        "n12",
    }


def test_user_names_with_dots_are_not_rejected(cluster):
    ref = cluster.reference("-u", "j.doe")
    assert "Multiple usernames" in ref.stdout
    py = cluster.pestat("-u", "j.doe")
    assert (py.returncode, py.stderr) == (1, "pestat: error: unknown user 'j.doe'\n")


def test_array_tasks_show_their_array_id(cluster):
    cluster.write(snapshot([node("n1")], [job(201, "n1", JobArrayID="200_1")]))
    ref = cluster.reference()
    assert "JobID(JobArrayID)" in ref.stdout
    assert ref_rows(ref)["n1"]["job_ids"] == ["201"]
    assert (
        nodes_of(cluster.pestat("-o", "json"))["n1"]["jobs"][0]["array_id"] == "200_1"
    )
    assert "200_1" in cluster.pestat().stdout


def test_default_partition_mark_stays_on_the_default(cluster):
    cluster.write(
        snapshot(
            [
                node(
                    "n1",
                    "short*",
                    StateCompact="alloc",
                    CPUsState="8/0/0/8",
                    CPUsLoad="8.00",
                ),
                node("n1", "long"),
            ],
            [job(1, "n1", Partition="long")],
        )
    )
    assert ref_rows(cluster.reference())["n1"]["partition"] == "long*"
    assert cluster.pestat().stdout.splitlines()[1].split()[1] == "short*,long"


def test_last_job_end_is_not_repeated_per_partition_line(cluster):
    cluster.write(
        snapshot(
            [
                node(
                    "n1",
                    "short*",
                    CPUsState="2/6/0/8",
                    CPUsLoad="2.00",
                    StateCompact="mix",
                ),
                node("n1", "long"),
            ],
            [job(1, "n1"), job(2, "n1", EndTime="2026-10-26T08:00:00")],
        )
    )
    second = cluster.reference("-2", "-E").stdout.splitlines()[-1]
    assert second.count("LASTJOB=") == 2
    lines = cluster.pestat("-2", "-E").stdout.splitlines()
    assert [line.count("last end") for line in lines[1:3]] == [1, 1]


def test_a_job_that_just_started_still_gets_the_grace_period(cluster):
    cluster.write(
        snapshot(
            [node("n1", CPUsState="8/0/0/8", CPUsLoad="1.00", StateCompact="alloc")],
            [
                job(1, "n1", TimeUsed="0:00", NumNodes="1"),
                job(2, "n1", TimeUsed="2:00:00"),
            ],
        )
    )
    assert "n1" in ref_rows(cluster.reference("-f"))
    assert nodes_of(cluster.pestat("-o", "json", "-f")) == {}


def test_unknown_load_is_not_flagged(cluster):
    cluster.write(
        snapshot(
            [node("n1", CPUsState="8/0/0/8", CPUsLoad="N/A", StateCompact="alloc")]
        )
    )
    ref = cluster.reference()
    assert ref_rows(ref)["n1"]["flags"]["load"]
    assert " 0.00*" in ref.stdout
    n1 = nodes_of(cluster.pestat("-o", "json"))["n1"]
    assert (n1["cpu_load"], n1["flags"]["load"]) == (None, None)
    assert "N/A" in cluster.pestat().stdout


def test_unknown_free_memory_matches_no_filter(cluster):
    cluster.write(snapshot([node("n1", FreeMem="N/A")]))
    assert "n1" in ref_rows(cluster.reference("-M", "100"))
    assert nodes_of(cluster.pestat("-o", "json", "-M", "100")) == {}


def test_zero_threads_per_core_does_not_crash(cluster):
    cluster.write(
        snapshot(
            [
                node(
                    "n1",
                    CPUsState="4/4/0/8",
                    CPUsLoad="4.00",
                    StateCompact="mix",
                    Threads="0",
                )
            ]
        )
    )
    assert "division by zero" in cluster.reference().stderr
    assert "n1" in nodes_of(cluster.pestat("-o", "json"))


def test_critical_only_skips_warning_job_flags(cluster):
    cluster.write(
        snapshot(
            [
                node("n1", CPUsState="4/4/0/8", CPUsLoad="4.00", StateCompact="mix"),
                node("n2", CPUsState="4/4/0/8", CPUsLoad="4.00", StateCompact="mix"),
            ],
            [job(1, "n[1-2]", NumNodes="2")],
        )
    )
    assert set(ref_rows(cluster.reference("-F"))) == {"n1", "n2"}
    assert nodes_of(cluster.pestat("-o", "json", "-F")) == {}
    assert (
        nodes_of(cluster.pestat("-o", "json", "-f"))["n1"]["flags"]["jobs"] == "magenta"
    )


def test_jobs_in_hidden_partitions_show_on_shared_nodes(cluster):
    cluster.write(
        snapshot(
            [
                node("n1", CPUsState="8/0/0/8", CPUsLoad="8.00", StateCompact="alloc"),
                node(
                    "n1",
                    "secret",
                    hidden=True,
                    CPUsState="8/0/0/8",
                    CPUsLoad="8.00",
                    StateCompact="alloc",
                ),
            ],
            [job(1, "n1", Partition="secret")],
        )
    )
    assert ref_rows(cluster.reference())["n1"]["job_ids"] == []
    assert [
        j["job_id"] for j in nodes_of(cluster.pestat("-o", "json"))["n1"]["jobs"]
    ] == ["1"]


def test_shell_variables_do_not_leak_in(cluster):
    assert set(ref_rows(cluster.reference(env={"username": "root"}))) == {"n03", "n10"}
    assert len(nodes_of(cluster.pestat("-o", "json", env={"username": "root"}))) == 12


def test_maintenance_nodes_show_when_asked_for(cluster):
    cluster.write(snapshot([node("n1", StateCompact="maint")]))
    assert ref_rows(cluster.reference("-t", "maint")) == {}
    assert ref_rows(cluster.reference()) == {}
    assert set(nodes_of(cluster.pestat("-o", "json", "-t", "maint"))) == {"n1"}
    assert nodes_of(cluster.pestat("-o", "json")) == {}


def test_nodes_and_jobs_filters_intersect(cluster):
    assert set(ref_rows(cluster.reference("-n", "n01", "-j", "110"))) == {"n11", "n12"}
    assert set(nodes_of(cluster.pestat("-o", "json", "-n", "n11", "-j", "110"))) == {
        "n11"
    }
    assert nodes_of(cluster.pestat("-o", "json", "-n", "n01", "-j", "110")) == {}


def test_squeue_failure_is_an_error(cluster):
    cluster.write({**clean_cluster(), "fail": ["squeue"]})
    ref = cluster.reference()
    assert ref.returncode == 0
    assert all(r["job_ids"] == [] for r in parse_reference(ref.stdout))
    py = cluster.pestat()
    assert py.returncode == 1
    assert "squeue: error: Unable to contact slurm controller" in py.stderr


def test_zero_free_memory_threshold_is_a_usage_error(cluster):
    assert set(ref_rows(cluster.reference("-m", "0"))) == set(
        ref_rows(cluster.reference("-F"))
    )
    assert cluster.pestat("-m", "0").returncode == 2


def test_hostlists_are_not_globbed(cluster):
    (cluster.cwd / "n01").touch()
    assert set(ref_rows(cluster.reference("-n", "n0[1-2]"))) == {"n01"}
    assert set(nodes_of(cluster.pestat("-o", "json", "-n", "n0[1-2]"))) == {
        "n01",
        "n02",
    }


def test_closed_pipe_exits_quietly(cluster):
    cluster.write(snapshot([node(f"n{i}") for i in range(3000)]))
    env = {"PATH": f"{cluster.bin}:{os.environ['PATH']}", "HOME": str(cluster.home)}
    result = subprocess.run(
        f"{sys.executable} -m pestat -o json | head -c 1",
        shell=True,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.stderr == ""


def test_not_responding_and_failed_states_are_flagged(cluster):
    cluster.write(
        snapshot([node("n1", StateCompact="down*"), node("n2", StateCompact="fail")])
    )
    assert ref_rows(cluster.reference("-f")) == {}
    assert set(nodes_of(cluster.pestat("-o", "json", "-f"))) == {"n1", "n2"}


def test_grace_period_hides_only_the_load_flag(cluster):
    cluster.write(
        snapshot(
            [
                node(
                    "n1",
                    CPUsState="8/0/0/8",
                    CPUsLoad="1.00",
                    StateCompact="alloc",
                    FreeMem="1000",
                )
            ],
            [job(1, "n1", TimeUsed="5:00")],
        )
    )
    ref = cluster.reference()
    assert ref_rows(ref)["n1"]["flags"]["load"]
    assert ref_rows(cluster.reference("-f")) == {}
    n1 = nodes_of(cluster.pestat("-o", "json", "-f"))["n1"]
    assert n1["flags"]["load"] is None
    assert n1["flags"]["memory"] == "red"


def test_notices_are_gone_and_warnings_use_stderr(cluster):
    assert cluster.reference("-p", "long").stdout.startswith(
        "Print only nodes in partition long"
    )
    py = cluster.pestat("-p", "long,foobar")
    assert py.stdout.startswith("HOST")
    assert py.stderr == "pestat: warning: no nodes matched partition 'foobar'\n"
