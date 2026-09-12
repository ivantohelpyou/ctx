---
id: exp-002
title: Budget is an argument, not a hardcoded limit
summary: ctx get fills to roughly N tokens (chars/4) with the highest-ranked entries, then lists what it dropped.
tags: [retrieval, budget]
terms: [budget, token limit, dropped entries]
date: 2026-09-02
last-verified: 2026-09-12
covers:
  - src/budget.py
---

# Budget is an argument

Callable at different costs from the same index.
