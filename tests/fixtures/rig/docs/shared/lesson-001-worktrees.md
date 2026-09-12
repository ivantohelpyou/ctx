---
id: shared-001
title: Use worktrees in shared rig copies
summary: Never git-checkout in a shared crew/ivan directory; it moves HEAD out from under other sessions. Use a worktree instead.
tags: [git, concurrency]
terms: [worktree, shared checkout, HEAD]
date: 2026-09-01
---

# Use worktrees in shared rig copies

A shared working copy is read by more than one session; checking out a branch
in it moves everyone's HEAD.
