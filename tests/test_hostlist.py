import shutil
import subprocess

import pytest

from pestat.hostlist import expand

# Each expected value was checked against `scontrol show hostnames` on zlurm.
CASES = {
    "foo": ["foo"],
    "z[01-03,10]": ["z01", "z02", "z03", "z10"],
    "n[1-3],m[01-02]": ["n1", "n2", "n3", "m01", "m02"],
    "x[9-11]": ["x9", "x10", "x11"],
    "x[098-101]": ["x098", "x099", "x100", "x101"],
    "a[1-2],b": ["a1", "a2", "b"],
    "r1-n[1-2]": ["r1-n1", "r1-n2"],
    "g[1-2]-ib": ["g1-ib", "g2-ib"],
    "a[1-2]b[3-4]": ["a1b3", "a1b4", "a2b3", "a2b4"],
    "x[1,1]": ["x1", "x1"],
    "x[01-1]": ["x01"],
    "z[1014,1016-1018]": ["z1014", "z1016", "z1017", "z1018"],
}


@pytest.mark.parametrize(("expr", "hosts"), CASES.items(), ids=list(CASES))
def test_expand_matches_scontrol(expr, hosts):
    assert expand(expr) == hosts


def test_expand_keeps_padding_past_the_start_width():
    assert expand("x[098-1001]")[-3:] == ["x999", "x1000", "x1001"]


@pytest.mark.parametrize("expr", ["a[3-1]", "z[1-", "z1]x["])
def test_expand_rejects_bad_expressions(expr):
    with pytest.raises(ValueError):
        expand(expr)


@pytest.mark.live
@pytest.mark.skipif(shutil.which("scontrol") is None, reason="needs scontrol")
@pytest.mark.parametrize("expr", list(CASES), ids=list(CASES))
def test_expand_agrees_with_live_scontrol(expr):
    out = subprocess.run(
        ["scontrol", "show", "hostnames", expr],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert expand(expr) == out.split()
