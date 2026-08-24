# Branches and worktrees

`tools/worktrees.ps1` is the implementation; this is why it refuses things.

The repo's history is linear and its default is trunk. Small changes go on
`main`, because the cost of a branch is not the branch -- it is the branch
nobody closed. This project has already produced one of those: a worktree from
2026-08-21 (`worktree-agent-a2062d233325c94d3`) was still checked out two days
later, fully merged, 24 commits behind, with nothing on it that `main` did not
already have. Nothing was lost and nothing was gained; it was just there,
another copy of the tree to search by accident and another name in
`git branch -a` to wonder about.

## The rules

| Rule | Enforced by |
|---|---|
| One branch per **category**, `interior/<category>`, reused for every task in it | `open` names the branch from the category; there is no `-Branch` |
| At most **2 open** at a time | `open` refuses the third and names what to land |
| **Land, don't leave**: test, rebase, fast-forward, delete branch, remove worktree | `land`, in one command |
| **Merged means gone** | `prune` reaps anything already in `main` |
| Unmerged work older than **3 days** is reported until it is dealt with | `list` prints `STALE:` |
| Never force-delete unmerged commits | `prune` keeps anything ahead of `main` unless `-Force` |

A category is a *line of work*, not a task: `interior/scene` holds every change
to the scene pipeline, whether that is one commit or six. Cutting a branch per
task is what produces the pile. The WIP limit is 2 rather than 1 because one
category often has to wait on a question -- an in-game measurement, a probe --
and the second slot is for the work that can proceed meanwhile.

## Why rebase and not merge

`land` rebases the branch onto `main` and then fast-forwards. Every commit in
`git log --oneline` here is a sentence about the map, in order; a merge commit
says "two people were working" and this repo has one. Fast-forward is the only
merge that keeps that true, and rebasing first is what makes it always
available.

## Where they live

`.claude/worktrees/<prefix>/<category>`. That path is already in `.gitignore`,
so a worktree never shows up as untracked junk in the parent's `git status` --
which is the other way worktrees go wrong: a `git add -A` from the parent that
sweeps a whole second checkout into a commit.

**A worktree does not get `catalog.json`.** It is gitignored -- built from the
local TaleSpire install and not portable -- so anything in a worktree that
resolves assets needs `--catalog ../../../../catalog.json` or an absolute path
to the parent's copy. Without it the catalog is rebuilt from the install, which
works but takes a minute and writes a second copy.

## Never `git add -A` in a checkout somebody else is using

This is not hypothetical. A docs commit made with `git add -A` on `main` swept
up an in-progress multi-slab feature -- `slab.py`, `cli.py` and a test -- that
the repo's owner had open at that moment, and committed it under a message
about documentation. It was split back out with

```bash
git reset --soft HEAD~1
```

and then `git restore --staged` on the files that were not mine, but the
uncommitted work had already been staged by the sweep, so its staged/unstaged
split did not survive exactly.

**Stage by path.** `git add docs/scenes.md CLAUDE.md`, and read
`git status --short` before committing: anything modified that you did not
touch belongs to whoever else is in the tree. This is the same failure the
worktree layout guards against, arriving from the other direction -- the
gitignored `.claude/worktrees` path stops the parent sweeping a worktree, and
nothing but care stops a commit sweeping a colleague.

## Commands

```powershell
.\tools\worktrees.ps1 list
```
```powershell
.\tools\worktrees.ps1 open -Name scene
```
```powershell
.\tools\worktrees.ps1 land -Name scene
```
```powershell
.\tools\worktrees.ps1 prune -Force
```

`land` runs `python -m pytest -q <worktree>` first and refuses to land a
failing branch. `-NoTest` skips it, for a branch that only touches docs.
