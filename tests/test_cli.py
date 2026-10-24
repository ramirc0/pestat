import json

import pytest

from pestat.cli import build_parser, main, read_config


def nodes_of(result):
    assert result.returncode == 0, result.stderr
    return [n["hostname"] for n in json.loads(result.stdout)["nodes"]]


def test_partition_list_and_last_value_wins():
    opts = build_parser().parse_args(["-p", "a", "-p", "gpu,,gpuh200"])
    assert opts.partition == ["gpu", "gpuh200"]


@pytest.mark.parametrize(
    "args", [["-f", "-m", "5"], ["-F", "-M", "5"], ["-m", "-3"], ["-x"]]
)
def test_usage_errors_exit_2(args):
    with pytest.raises(SystemExit) as exit_info:
        main(args)
    assert exit_info.value.code == 2


def test_version_exits_0(capsys):
    assert main(["-V"]) == 0
    assert capsys.readouterr().out.startswith("pestat 0.")


def test_read_config_converts_values(tmp_path):
    path = tmp_path / "pestat.conf"
    path.write_text(
        "# defaults\n"
        "export cpuload_delta1=3.5\t# CPU load delta\n"
        "uniquenodes=0\n"
        'hostlist="-n node[1-9]"\n'
        "username=$PESTAT_TEST_USER\n"
        "flaglevel=-1\n"
    )
    import os

    os.environ["PESTAT_TEST_USER"] = "alice"
    try:
        settings = read_config(str(path))
    finally:
        del os.environ["PESTAT_TEST_USER"]
    assert settings == {
        "cpuload_delta1": 3.5,
        "uniquenodes": False,
        "hostlist": "node[1-9]",
        "username": ["alice"],
        "flaglevel": None,
    }


def test_read_config_warns_and_skips_bad_lines(tmp_path, capsys):
    path = tmp_path / "pestat.conf"
    path.write_text("if true; then\nbogus=1\njobgracetime=soon\ncolors=0\n")
    assert read_config(str(path)) == {"colors": False}
    err = capsys.readouterr().err
    assert f"{path}:1: expected NAME=value" in err
    assert "unknown setting 'bogus'" in err
    assert "bad value 'soon' for jobgracetime" in err


def test_missing_config_gives_no_settings(tmp_path):
    assert read_config(str(tmp_path / "missing.conf")) == {}


def test_config_sets_defaults_and_flags_override(cluster):
    with open(cluster.home / ".pestat.conf", "a") as conf:
        conf.write('partition="-p gpu"\n')
    assert nodes_of(cluster.pestat("-o", "json")) == ["n08", "n09"]
    assert nodes_of(cluster.pestat("-o", "json", "-p", "short")) == [
        "n01",
        "n02",
        "n07",
    ]


def test_unknown_group_is_an_error(cluster):
    result = cluster.pestat("-g", "no-such-group-xyz")
    assert (result.returncode, result.stderr) == (
        1,
        "pestat: error: unknown group 'no-such-group-xyz'\n",
    )
    assert cluster.calls() == []


@pytest.mark.parametrize("jobs", ["999", "999,998"])
def test_jobs_not_running_is_an_error(cluster, jobs):
    result = cluster.pestat("-j", jobs)
    assert result.returncode == 1
    assert result.stderr == f"pestat: error: jobs {jobs} are not running\n"


def test_array_base_selects_all_tasks(cluster):
    snapshot = json.loads(cluster.state.read_text())
    snapshot["jobs"][0].update(JobID="301", JobArrayID="300_1")
    snapshot["jobs"][1].update(JobID="302", JobArrayID="300_2")
    cluster.write(snapshot)
    assert nodes_of(cluster.pestat("-o", "json", "-j", "300")) == ["n02", "n03"]


def test_selection_without_jobs_warns(cluster):
    result = cluster.pestat("-A", "nosuch")
    assert result.returncode == 0
    assert (
        result.stderr == "pestat: warning: no running jobs matched account 'nosuch'\n"
    )


def test_unmatched_partitions_warn_and_continue(cluster):
    result = cluster.pestat("-o", "json", "-p", "gpu,foobar,nada")
    assert nodes_of(result) == ["n08", "n09"]
    assert result.stderr.splitlines() == [
        "pestat: warning: no nodes matched partition 'foobar'",
        "pestat: warning: no nodes matched partition 'nada'",
    ]


def test_named_hidden_partition_shows(cluster):
    assert nodes_of(cluster.pestat("-o", "json", "-p", "secret")) == ["n13"]


def test_sinfo_errors_are_reported(cluster):
    result = cluster.pestat("-t", "bogus")
    assert result.returncode == 1
    assert (
        result.stderr
        == "pestat: error: sinfo: sinfo: error: Invalid node state: bogus\n"
    )


def test_missing_slurm_commands_are_reported(cluster):
    (cluster.home / ".pestat.conf").write_text("")
    result = cluster.pestat(env={"PATH": str(cluster.cwd)})
    assert (result.returncode, result.stderr) == (
        1,
        "pestat: error: cannot run sinfo: No such file or directory\n",
    )
