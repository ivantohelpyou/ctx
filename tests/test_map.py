"""`ctx map`: the same hits as `ctx get`, the grep baseline's arithmetic, and
the promise that none of it writes."""

import hashlib
import json
import threading
import urllib.error
import urllib.request

import pytest
from click.testing import CliRunner

from ctx_cli.cli import main
from ctx_cli.corpus import load_config
from ctx_cli.ctxdb.db import log_query
from ctx_cli.indexer import build_index
from ctx_cli.mapview import (
    GREP_TOP_FILES,
    grep_baseline,
    history_log,
    make_server,
    open_rigs,
    query_all,
    run_query,
    snapshot,
    tokens,
)


def _write(rig_dir, name, doc_id, title, summary):
    (rig_dir / "docs" / "shared" / f"{name}.md").write_text(
        f'---\nid: {doc_id}\ntitle: "{title}"\nsummary: "{summary}"\n---\n'
    )


@pytest.fixture
def built(config):
    build_index(config)
    return config


def _get_output(rig_dir, *args):
    result = CliRunner().invoke(main, ["get", *args, "--dir", str(rig_dir)])
    assert result.exit_code == 0, result.output
    return result.output


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- parity with `ctx get` -----------------------------------------------


@pytest.mark.parametrize(
    "query, extra",
    [
        ("budget", []),
        ("database", []),
        ("database", ["--history"]),
        ("database", ["--corpus", "decisions"]),
        ("fts5 bm25 stemming", ["--budget", "20"]),
        ("worktree checkout", ["--limit", "1"]),
        ("zzzznothingmatches", []),
    ],
)
def test_map_query_prints_exactly_what_ctx_get_prints(rig_dir, built, query, extra):
    opts = {}
    if "--history" in extra:
        opts["history"] = True
    if "--corpus" in extra:
        opts["corpus"] = extra[extra.index("--corpus") + 1]
    if "--budget" in extra:
        opts["budget"] = int(extra[extra.index("--budget") + 1])
    if "--limit" in extra:
        opts["limit"] = int(extra[extra.index("--limit") + 1])

    mapped = run_query(built, query, **opts)

    # `ctx get` runs second: it logs the query, the map must not have.
    assert mapped["text"] == _get_output(rig_dir, query, *extra)


def test_kept_dropped_and_weak_are_ctx_gets_lines(rig_dir):
    for i in range(8):
        _write(rig_dir, f"w{i}", f"w-{i}", f"Widget config {i}", f"widget configuration {i}")
    _write(rig_dir, "lone", "lone-1", "A lone gadget", "gadget only")
    config = load_config(rig_dir / ".ctx")
    build_index(config)

    wide = run_query(config, "widget config", limit=3)
    lines = _get_output(rig_dir, "widget config", "--limit", "3").splitlines()
    assert [h["id"] for h in wide["kept"]] == [
        line.split(" — ")[0] for line in lines if not line.startswith("dropped:")
    ]
    assert len(wide["dropped"]) == 5

    weak = run_query(config, "gadget unrelated")  # one word of two: below the floor
    assert weak["miss"] and not weak["kept"]
    assert [h["id"] for h in weak["weak"]] == ["lone-1"]
    assert weak["text"].startswith("weak: lone-1 — ")


def test_ctx_tokens_is_chars_over_four_of_the_printed_output(built):
    result = run_query(built, "budget")
    assert result["ctx_tokens"] == len(result["text"]) // 4 > 0


# --- the grep baseline ----------------------------------------------------


def _corpus(tmp_path, files):
    from ctx_cli.corpus import CorpusSpec

    d = tmp_path / "corpus"
    d.mkdir()
    for name, text in files.items():
        (d / name).write_text(text)
    return [CorpusSpec(name="c", type="shared", path=d)]


def test_grep_counts_files_with_any_keyword_and_reads_the_top_three(tmp_path):
    big = "alpha beta " * 100  # 2 keywords, 200 matches
    mid = "alpha beta " * 10
    small = "alpha beta"
    one = "alpha only " * 3  # 1 keyword
    none = "nothing relevant here"
    specs = _corpus(
        tmp_path, {"big.md": big, "mid.md": mid, "small.md": small, "one.md": one, "none.md": none}
    )
    r = grep_baseline("alpha beta", specs)
    assert r["keywords"] == ["alpha", "beta"]
    assert r["files"] == 4  # none.md does not match
    # top 3 by keywords matched, then match count: big, mid, small
    assert [p.rsplit("/", 1)[1] for p in r["top"]] == ["big.md", "mid.md", "small.md"]
    assert len(r["top"]) == GREP_TOP_FILES
    listing = "\n".join(str(tmp_path / "corpus" / n) for n in sorted(
        ["big.md", "mid.md", "small.md", "one.md"]))
    assert r["tokens"] == tokens(listing) + tokens(big + mid + small)


def test_grep_ignores_short_words_and_stopwords(tmp_path):
    specs = _corpus(tmp_path, {"a.md": "with from this that page cat dog"})
    r = grep_baseline("with from this that page cat dog", specs)
    assert r["keywords"] == [] and r["files"] == 0 and r["tokens"] == 0


def test_grep_is_case_insensitive_and_scoped_to_the_corpus_asked_for(rig_dir, built):
    everything = run_query(built, "worktree")["grep"]
    only_explainers = run_query(built, "worktree", corpus="explainers")["grep"]
    assert everything["files"] >= 1
    assert only_explainers["files"] == 0  # the worktree lesson lives in shared
    assert run_query(built, "WORKTREE")["grep"]["files"] == everything["files"]


def test_query_all_totals_every_rig(rig_dir, built, tmp_path):
    other = tmp_path / "other"
    (other / ".ctx").mkdir(parents=True)
    (other / ".ctx" / "config.yaml").write_text(
        "corpora:\n  shared:\n    type: shared\n    path: docs\n"
    )
    (other / "docs").mkdir()
    (other / "docs" / "n.md").write_text(
        '---\nid: n-1\ntitle: "Budget notes"\nsummary: "budget budget"\n---\n'
    )
    other_cfg = load_config(other / ".ctx")
    build_index(other_cfg)

    both = query_all([built, other_cfg], "budget")
    parts = [run_query(built, "budget"), run_query(other_cfg, "budget")]
    assert both["ctx_tokens"] == sum(p["ctx_tokens"] for p in parts)
    assert both["grep_files"] == sum(p["grep"]["files"] for p in parts)
    assert both["grep_tokens"] == sum(p["grep"]["tokens"] for p in parts)
    assert not both["miss"]


def test_a_rig_without_an_index_is_reported_not_fatal(config):
    out = query_all([config], "budget")  # never built
    assert out["rigs"][0]["error"] == "no index.db"
    assert out["miss"]
    assert snapshot([config])["rigs"][0]["error"]


# --- the snapshot and the history strip -----------------------------------


def test_snapshot_groups_by_corpus_and_marks_superseded_docs_old(built):
    rig = snapshot([built])["rigs"][0]
    by_name = {c["name"]: c for c in rig["corpora"]}
    decisions = {d["id"]: d for d in by_name["decisions"]["docs"]}
    assert decisions["adr-001"]["current"] is False  # replaced by adr-002
    assert decisions["adr-002"]["current"] is True
    assert by_name["decisions"]["links"] == [{"from": "adr-002", "to": "adr-001"}]
    assert {d["id"] for d in by_name["explainers"]["docs"]} == {"exp-001", "exp-002"}
    assert rig["stale"] is False


def test_a_chain_is_laid_out_contiguously(rig_dir):
    for n in range(4):
        _write(rig_dir, f"x{n}", f"x-{n}", f"Doc {n}", "filler")
    config = load_config(rig_dir / ".ctx")
    build_index(config)
    docs = snapshot([config])["rigs"][0]["corpora"]
    ids = [d["id"] for c in docs if c["name"] == "decisions" for d in c["docs"]]
    assert ids in (["adr-002", "adr-001"], ["adr-001", "adr-002"])


def test_snapshot_flags_a_stale_index(rig_dir, built):
    _write(rig_dir, "late", "late-1", "Late arrival", "added after the build")
    assert snapshot([built])["rigs"][0]["stale"] is True


def test_history_returns_logged_queries_and_misses(built):
    log_query(built.ctx_db_path, query="budget", budget=1500, returned=2)
    log_query(built.ctx_db_path, query="nothing here", budget=1500, returned=0)
    h = history_log([built])
    assert [q["query"] for q in h["queries"]] == ["budget", "nothing here"]
    assert [m["query"] for m in h["misses"]] == ["nothing here"]
    assert h["queries"][0]["returned"] == 2


def test_history_without_a_ctx_db_is_empty(built):
    assert not built.ctx_db_path.exists()
    assert history_log([built]) == {"queries": [], "misses": []}
    assert not built.ctx_db_path.exists()  # looking did not create it


# --- read-only ------------------------------------------------------------


def test_nothing_is_written_by_map_queries(built):
    index_before = _digest(built.index_db_path)
    run_query(built, "budget")
    query_all([built], "zzzznothing")
    snapshot([built])
    history_log([built])
    assert _digest(built.index_db_path) == index_before
    assert not built.ctx_db_path.exists()  # a map query is not logged


def test_a_stale_index_is_read_as_it_is_not_rebuilt(rig_dir, built):
    _write(rig_dir, "late", "late-1", "Late arrival", "unique zebra marker")
    before = _digest(built.index_db_path)
    assert run_query(built, "zebra marker")["miss"]  # ctx get would rebuild and find it
    assert _digest(built.index_db_path) == before


# --- the server -----------------------------------------------------------


@pytest.fixture
def served(built):
    server = make_server([built])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _fetch(url):
    with urllib.request.urlopen(url) as r:
        return r.status, r.headers["Content-Type"], r.read()


def test_server_serves_the_page_and_the_json_endpoints(rig_dir, served):
    status, ctype, body = _fetch(served + "/")
    assert status == 200 and ctype.startswith("text/html")
    assert body.lstrip().lower().startswith(b"<!doctype html>")

    _, _, body = _fetch(served + "/api/map")
    assert json.loads(body)["rigs"][0]["corpora"]

    _, _, body = _fetch(served + "/api/query?q=database&history=1")
    res = json.loads(body)
    assert res["rigs"][0]["text"] == _get_output(rig_dir, "database", "--history")
    assert res["ctx_tokens"] == tokens(res["rigs"][0]["text"])

    _, _, body = _fetch(served + "/api/history")
    assert set(json.loads(body)) == {"queries", "misses"}


def test_server_rejects_an_empty_query_and_unknown_paths(served):
    with pytest.raises(urllib.error.HTTPError) as e:
        _fetch(served + "/api/query?q=%20")
    assert e.value.code == 400
    with pytest.raises(urllib.error.HTTPError) as e:
        _fetch(served + "/nope")
    assert e.value.code == 404


def test_server_binds_loopback_only(built):
    server = make_server([built])
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()


def test_bad_numbers_in_the_query_string_fall_back_to_defaults(served):
    _, _, body = _fetch(served + "/api/query?q=budget&budget=abc&limit=")
    assert json.loads(body)["rigs"][0]["kept"]


# --- the command ----------------------------------------------------------


def test_open_rigs_dedupes_directories_in_one_rig(rig_dir):
    rigs = open_rigs([rig_dir, rig_dir / "docs", rig_dir / "docs" / "shared"])
    assert len(rigs) == 1


def test_map_command_outside_a_rig_is_a_clean_error(tmp_path):
    result = CliRunner().invoke(main, ["map", "--dir", str(tmp_path)])
    assert result.exit_code != 0
    assert "no .ctx/config.yaml" in result.output
