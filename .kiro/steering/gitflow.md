---
inclusion: always
---

# GitFlow Workflow

This project follows a GitFlow-inspired branching model. Use these conventions for all
version-control work so history stays clean, releases are traceable, and CI behaves
predictably.

## Branch Model

### Long-lived branches
- `main` — the production / canonical branch. Always deployable. The scheduled workflows
  (`python -m stock_watch ...`) and CI run against `main`. Never commit experimental work
  directly here.
- `develop` — integration branch for the next release. Feature branches merge here first.
  If the team prefers a trunk-based simplification, `develop` may be omitted and feature
  branches may target `main` via PR (see "Lightweight mode" below).

### Supporting (short-lived) branches
Branch off the indicated base, merge back via Pull Request, then delete:

| Type      | Prefix      | Branch from | Merge back into     | Purpose                                   |
|-----------|-------------|-------------|---------------------|-------------------------------------------|
| Feature   | `feature/`  | `develop`   | `develop`           | New signals, reports, strategy changes    |
| Fix       | `fix/`      | `develop`   | `develop`           | Non-urgent bug fixes                       |
| Release   | `release/`  | `develop`   | `main` + `develop`  | Stabilize, bump version, finalize docs    |
| Hotfix    | `hotfix/`   | `main`      | `main` + `develop`  | Urgent production fixes                    |
| Chore     | `chore/`    | `develop`   | `develop`           | Tooling, deps, refactors with no behavior |

### Naming
- Use lowercase, hyphen-separated, descriptive names: `feature/relative-strength-signal`,
  `fix/backtest-fee-calc`, `hotfix/telegram-token-error`.
- Optionally prefix with an issue id: `feature/42-rs-signal`.

## Lightweight mode (current default for this repo)

This repo currently operates on a single `main` branch and has historically allowed direct
`commit + push` for verified changes. When working in lightweight mode:

- Create a `feature/`, `fix/`, or `chore/` branch off `main` for any non-trivial change.
- Open a Pull Request into `main` for review instead of pushing directly, unless the user
  explicitly asks to push to `main`.
- Reserve direct pushes to `main` for trusted, already-verified maintenance (e.g. watchlist
  data updates), matching the existing operator habit.

## Commit Conventions

- Write clear, imperative-mood subject lines under ~70 characters:
  `Add relative strength vs TWII signal`.
- Group related changes into focused commits; avoid mixing refactors with behavior changes.
- Reference issues where relevant: `Fix ATR band rounding (#57)`.
- Do not commit unrelated local data files. Stage specific files by name rather than
  `git add -A` / `git add .` so stray artifacts are not pushed.
- Automated artifact commits made by CI use the `[skip ci]` suffix to avoid recursive runs
  (e.g. `Update stock watch artifacts [skip ci]`). Keep this convention for bot commits.

## Pull Request Rules

- Target `develop` for features/fixes; target `main` only for release/hotfix branches
  (or per lightweight mode above).
- PR title is concise; PR body summarizes what changed, what was tested, and known
  limitations.
- All tests must pass before merge: `python scripts/run_unittest_quiet.py`.
- Keep PRs small and reviewable. Split large refactors (e.g. breaking up the legacy
  `daily_theme_watchlist.py`) into incremental PRs.
- Delete the source branch after merge.

## Release Process

1. Branch `release/x.y.z` from `develop`.
2. Finalize: bump version, update docs/changelog, run full test suite and a dry-run of
   `python -m stock_watch daily --mode preopen --force-watchlist`.
3. Merge into `main` and tag the release: `vX.Y.Z`.
4. Merge `main` back into `develop` so the version bump and any fixes propagate.

## Hotfix Process

1. Branch `hotfix/x.y.z` from `main`.
2. Apply the minimal fix, add a regression test, run the suite.
3. Merge into `main`, tag the patch release.
4. Merge back into `develop`.

## Do / Don't

- Do rebase or merge the latest base branch into your feature branch before opening a PR
  to keep history current.
- Do not force-push to `main` or `develop`.
- Do not skip CI hooks (`--no-verify`) or amend commits after a failing hook; fix, re-stage,
  and create a new commit instead.
- Do not merge the historical `testv` branch wholesale back into `main` (see handoff notes).
