import time

from ctx_cli.indexer import build_index
from ctx_cli.query import DEFAULT_BUDGET, apply_budget, render, search


def test_search_finds_by_terms(config):
    build_index(config)
    hits = search(config.index_db_path, "bm25")
    assert any(h.id == "exp-001" for h in hits)


def test_search_ranks_title_above_summary_only_match(config):
    build_index(config)
    # "budget" is in exp-002's title and terms; should rank at or near top.
    hits = search(config.index_db_path, "budget")
    assert hits[0].id == "exp-002"


def test_history_flag_includes_superseded_decision(config):
    build_index(config)
    default_hits = search(config.index_db_path, "database", corpus="decisions")
    assert all(h.id != "adr-001" for h in default_hits)  # superseded, hidden by default

    history_hits = search(config.index_db_path, "database", corpus="decisions", history=True)
    assert any(h.id == "adr-001" for h in history_hits)


def test_corpus_filter(config):
    build_index(config)
    hits = search(config.index_db_path, "worktree", corpus="shared")
    assert all(h.corpus == "shared" for h in hits)
    assert any(h.id == "shared-001" for h in hits)


def test_apply_budget_always_keeps_first_hit_even_if_oversized(config):
    build_index(config)
    hits = search(config.index_db_path, "database", corpus="decisions", history=True)
    kept, dropped = apply_budget(hits, budget=1)
    assert len(kept) == 1


def test_dropped_lines_rendered(config):
    build_index(config)
    hits = search(config.index_db_path, "database", corpus="decisions", history=True)
    kept, dropped = apply_budget(hits[:1], budget=10000)
    dropped_manual = hits[1:]
    output = render(kept, dropped_manual)
    for h in dropped_manual:
        assert f"dropped: {h.id} — {h.title}" in output


def test_output_contract_format(config):
    build_index(config)
    hits = search(config.index_db_path, "fts5")
    kept, dropped = apply_budget(hits, DEFAULT_BUDGET)
    output = render(kept, dropped)
    line = output.splitlines()[0]
    parts = line.split(" — ")
    assert len(parts) == 4  # id, title, path#slug, summary
    assert "#" in parts[2]


def test_byte_identical_output_after_rm_and_reindex(config):
    build_index(config)
    hits1 = search(config.index_db_path, "budget retrieval")
    kept1, dropped1 = apply_budget(hits1, DEFAULT_BUDGET)
    out1 = render(kept1, dropped1)

    config.index_db_path.unlink()
    build_index(config)
    hits2 = search(config.index_db_path, "budget retrieval")
    kept2, dropped2 = apply_budget(hits2, DEFAULT_BUDGET)
    out2 = render(kept2, dropped2)

    assert out1 == out2
    assert out1.encode("utf-8") == out2.encode("utf-8")


def test_query_latency_under_100ms(config):
    build_index(config)
    start = time.perf_counter()
    hits = search(config.index_db_path, "database decision architecture")
    apply_budget(hits, DEFAULT_BUDGET)
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert elapsed_ms < 100, f"ctx get took {elapsed_ms:.1f}ms, budget is 100ms"
