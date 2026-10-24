import pytest

from pestat import slurm


def sinfo_row(**fields):
    row = {
        "host": "z1",
        "partition": "long",
        "cpus": "4/4/0/8",
        "load": "1.00",
        "memory": "64000",
        "free": "32000",
        "state": "mix",
        "threads": "1",
        "gres": "(null)",
        "gres_used": "gpu:0",
        "reason": "none",
    }
    row.update(fields)
    return "|".join(row.values())


def test_parse_nodes_merges_partition_rows_in_order():
    nodes, default = slurm.parse_nodes(
        [
            sinfo_row(partition="short*"),
            sinfo_row(partition="long"),
            sinfo_row(host="z2"),
        ]
    )
    assert list(nodes) == ["z1", "z2"]
    assert nodes["z1"]["partitions"] == ["short", "long"]
    assert default == "short"


def test_parse_nodes_maps_unknown_values_to_none():
    [node] = slurm.parse_nodes([sinfo_row(load="N/A", free="N/A", threads="N/A")])[
        0
    ].values()
    assert (node["cpu_load"], node["free_mem_mb"], node["threads_per_core"]) == (
        None,
        None,
        None,
    )


def test_parse_nodes_keeps_reasons_with_pipes():
    [node] = slurm.parse_nodes([sinfo_row(reason="bad gpu | replace")])[0].values()
    assert node["reason"] == "bad gpu | replace"


@pytest.mark.parametrize(
    ("gres", "count"),
    [
        ("(null)", 0),
        ("gpu:0", 0),
        ("gpu:4", 4),
        ("gpu:h200:8(S:0-1)", 8),
        ("gpu:4(S:0-1)", 4),
        ("gpu:a100:3(IDX:0-2)", 3),
        ("gpu:a100:2(IDX:0,2),gpu:v100:1(IDX:0)", 3),
        ("shard:h200:64,gpu:h200:4", 4),
        ("gpu:a100:0(IDX:N/A)", 0),
    ],
)
def test_gpu_count_reads_every_gres_form(gres, count):
    assert slurm.gpu_count(gres) == count


def squeue_row(**fields):
    row = {
        "job_id": "7",
        "array_id": "7",
        "uid": "0",
        "gid": "0",
        "account": "(null)",
        "start": "2026-10-24T08:00:00",
        "end": "Unknown",
        "used": "5:06",
        "partition": "long",
        "num_nodes": "1",
        "tres": "cpu=1",
        "nodelist": "z1",
        "name": "my job | x",
    }
    row.update(fields)
    return "|".join(row.values())


def test_parse_jobs_keeps_names_with_spaces_and_pipes():
    [job] = slurm.parse_jobs([squeue_row()])
    assert job["name"] == "my job | x"
    assert (job["account"], job["end_time"], job["array_id"]) == (None, None, None)


@pytest.mark.parametrize(
    ("used", "seconds"),
    [
        ("0:00", 0),
        ("5:06", 306),
        ("1:00:00", 3600),
        ("2-03:04:05", 183845),
        ("INVALID", None),
    ],
)
def test_parse_jobs_converts_time_used(used, seconds):
    [job] = slurm.parse_jobs([squeue_row(used=used)])
    assert job["time_used_s"] == seconds


def test_parse_jobs_falls_back_to_numeric_ids():
    [job] = slurm.parse_jobs([squeue_row(uid="987654321", gid="987654321")])
    assert (job["user"], job["group"]) == ("987654321", "987654321")


def test_run_scrubs_slurm_environment(monkeypatch):
    monkeypatch.setenv("SQUEUE_USERS", "someone")
    monkeypatch.setenv("SINFO_PARTITION", "gpu")
    monkeypatch.setenv("SLURM_CLUSTERS", "other")
    monkeypatch.setenv("SLURM_TIME_FORMAT", "relative")
    [result] = slurm.run([["env"]])
    env = dict(line.split("=", 1) for line in result.stdout.splitlines())
    assert not {"SQUEUE_USERS", "SINFO_PARTITION", "SLURM_CLUSTERS"} & set(env)
    assert env["SLURM_TIME_FORMAT"] == "standard"


def test_run_starts_commands_concurrently():
    import time

    start = time.monotonic()
    slurm.run([["sleep", "0.3"], ["sleep", "0.3"], ["sleep", "0.3"]])
    assert time.monotonic() - start < 0.6


def test_run_raises_for_missing_commands():
    with pytest.raises(FileNotFoundError):
        slurm.run([["pestat-no-such-command"]])
