import csv
import io
import json

import pytest

from pestat.cli import build_parser
from pestat.output import RESET, TABLE_FIELDS, write_text


def snapshot(cluster, *args):
    doc = json.loads(cluster.pestat("-o", "json", *args).stdout)
    return doc["nodes"], doc["default_partition"]


def render(cluster, args=(), **kwargs):
    rows, default = snapshot(cluster, *args)
    out = io.StringIO()
    write_text(
        rows,
        build_parser().parse_args(list(args)),
        out,
        default_partition=default,
        **kwargs,
    )
    return out.getvalue()


TEXT_CASES = {
    "plain": ([], {}),
    "per-partition": (["-2"], {}),
    "details": (["-G", "-N", "-S", "-E", "-T", "-a"], {}),
    "colors": ([], {"colors": True}),
    "narrow": (["-N"], {"width": 80}),
    "no-gpus": (["-p", "long"], {}),
    "free-memory": (["-m", "20000"], {}),
    "empty": (["-u", "root", "-p", "gpu"], {}),
}


@pytest.mark.parametrize(("args", "kwargs"), TEXT_CASES.values(), ids=list(TEXT_CASES))
def test_text_layout(cluster, golden, args, kwargs, request):
    golden(request.node.callspec.id, render(cluster, args, **kwargs))


def test_cli_text_matches_the_renderer(cluster):
    assert cluster.pestat().stdout == render(cluster)


def test_narrow_lines_fit_the_width(cluster):
    lines = render(cluster, ["-N"], width=80).splitlines()
    assert max(len(line) for line in lines) <= 80
    assert [line[-3:] == "..." for line in lines[1:-2]] == [
        bool(line.split()[8:]) and len(line) == 80 for line in lines[1:-2]
    ]


def test_utf8_streams_get_a_single_character_ellipsis(cluster):
    rows, default = snapshot(cluster)
    raw = io.BytesIO()
    out = io.TextIOWrapper(raw, encoding="utf-8")
    write_text(
        rows, build_parser().parse_args([]), out, default_partition=default, width=60
    )
    out.flush()
    assert "…" in raw.getvalue().decode()


def test_colors_wrap_only_flagged_cells_and_header(cluster):
    text = render(cluster, colors=True)
    plain = render(cluster)
    assert text.replace(RESET, "").count("\033[1;31m") == 6
    for code in ("\033[1m", "\033[1;31m", "\033[1;35m", "\033[1;32m"):
        text = text.replace(code, "")
    assert text.replace(RESET, "") == plain


def test_json_is_one_document_with_nested_jobs(cluster):
    doc = json.loads(cluster.pestat("-o", "json").stdout)
    assert set(doc) == {"time", "default_partition", "nodes"}
    n10 = next(n for n in doc["nodes"] if n["hostname"] == "n10")
    assert [j["job_id"] for j in n10["jobs"]] == ["104", "105", "106"]
    assert n10["flags"] == {"state": None, "load": None, "jobs": "red", "memory": None}


@pytest.mark.parametrize(("fmt", "delimiter"), [("tsv", "\t"), ("csv", ",")])
def test_tables_have_one_row_per_node(cluster, fmt, delimiter):
    text = cluster.pestat("-o", fmt).stdout
    rows = list(csv.DictReader(io.StringIO(text), delimiter=delimiter))
    assert [r["hostname"] for r in rows] == [f"n{i:02d}" for i in range(1, 13)]
    assert list(rows[0]) == [
        *TABLE_FIELDS,
        "state_flag",
        "load_flag",
        "jobs_flag",
        "memory_flag",
        "job_ids",
        "users",
    ]
    n10 = next(r for r in rows if r["hostname"] == "n10")
    assert (n10["job_ids"], n10["jobs_flag"], n10["reason"]) == (
        "104,105,106",
        "red",
        "",
    )
    assert "\r" not in text


def test_text_is_the_default_and_structured_formats_print_no_footer(cluster):
    assert cluster.pestat().stdout.splitlines()[-1].startswith("12 nodes")
    assert "nodes," not in cluster.pestat("-o", "tsv").stdout
