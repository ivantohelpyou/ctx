---
id: adr-002
title: Two database files, the durable half as an ORM
summary: Same split as adr-001, plus the durable half is a SQLAlchemy models.py.
tags: [architecture]
terms: [index.db, ctx.db, sea]
status: accepted
date: 2026-09-05
supersedes: [adr-001]
superseded-by: []
---

# Two database files, the durable half as an ORM

Restates adr-001 and adds the SEA build note.
