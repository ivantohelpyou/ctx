---
id: exp-001
title: Why FTS5 over a vector index
summary: SQLite FTS5 with Porter stemming and BM25 is enough at a few hundred docs; no embeddings in v1.
tags: [search, sqlite]
terms: [fts5, bm25, full-text search]
date: 2026-09-01
last-verified: 2026-09-12
covers:
  - src/searcher.py
---

# Why FTS5 over a vector index

The corpora here are a few hundred one-line summaries, well below where a
vector index earns its dependency weight. FTS5 ships inside every Python's
`sqlite3` module.
