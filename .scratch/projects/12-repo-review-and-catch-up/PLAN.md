# 12 — Plan: land 0.9.0, refresh docs, then work the deferred backlog

Sequenced by value. **Steps 1–2 are the actual catch-up** (finish shipping the work that already
exists in code); steps 3+ are the genuine remaining feature backlog from `docs/PYJUTSU_CONCEPT.md`
§12 ("Later") and the 0.7.0 code review, ordered by consumer pull.

**Primary consumer throughout: gitman.** It already depends on pyjutsu in-process (`pyjutsu>=0.8`,
imported across 8 modules) and its `08-split-lane-capability` S3 (hunk-level split) tier is waiting on
0.9.0's `tx.split` reaching a release it can pin. Let gitman's needs set priority; keep pyjutsu
un-opinionated (no lane/workflow policy leaks — CONCEPT §10).

Conventions for every step: run in **devenv** (`devenv shell -- …`); route VCS through the repo's
tooling (this checkout is a plain colocated `.git`, jj/gitman live *inside* devenv — use `git` here,
branch off `main` first, commit incrementally); **do not push without an explicit ask**; **never run
bare `cargo fmt`** (the tree isn't fmt-clean under this devenv's rustfmt — it rewrites unrelated
files; hand-match surrounding style); no AI-authorship trailers. Verify green before every commit.

---

## Step 1 — Land 0.9.0 on `main` and release it *(the outstanding item — do first)*

**Why:** 0.9.0 (native `tx.split`/`select_tree`, adopt keep-ref prune, build-derived version guard)
is **complete and green but stranded on branch `feat/11-tx-split`.** `main` lacks it; there's no tag.
gitman's sub-file-split tier can't pin the `tx.split` it needs until 0.9.0 is a landed release.

**Deliverables / files:**
- Merge `feat/11-tx-split` → `main` (fast-forward if possible; otherwise a merge commit).
- Confirm 0.9.0 is the tip of `main`; tag `v0.9.0` if the repo's release convention uses tags
  (check `git tag` for the `vX.Y.Z` pattern seen in older bumps).
- Ensure the built wheel is available to gitman's resolver (per `gitman/pyproject.toml`: pyjutsu
  resolves from **vendomat's prebuilt wheelhouse** via `UV_FIND_LINKS`, not PyPI — so "release" here
  means the 0.9.0 wheel is in that wheelhouse / rebuilt, not a PyPI publish). Verify with vendomat's
  flow; if out of scope for this repo, hand off with a note.

**Acceptance:** `git log main` shows the 0.9.0 commit; `devenv shell -- python -m pytest tests/ -q`
green on `main` (224+); a fresh `devenv shell -- python -c "import pyjutsu; print(pyjutsu.__version__)"`
prints `0.9.0`; gitman can bump to `pyjutsu>=0.9` and resolve.

**Risks:** the merge is the gated action — **do not push without an explicit ask.** The wheelhouse/
vendomat step may belong to a different repo; don't guess — verify or defer with a clear note.

---

## Step 2 — Refresh the stale status headers (docs-only, cheap, high signal)

**Why:** README says *"Status: 0.8.0"* and `docs/PYJUTSU_CONCEPT.md`'s **Status** line still reads
M1/M2 = 0.2.0/0.3.0 binding **jj-lib 0.38**. The code is **0.9.0 / jj-lib 0.42.0**. This is pure
drift that misleads any reader (including future agents) about what's shipped. (The CONCEPT *body*
§5/§12 is already current — only the top-of-file Status paragraph is stale.)

**Deliverables / files:**
- `README.md` — Status header → 0.9.0; add one line that the power surface now covers **sub-file
  `split`/`select_tree`** (mirror the existing "Transactions & git" / "Escape hatch" prose style).
- `docs/PYJUTSU_CONCEPT.md` — update the top **Status** paragraph to reflect 0.9.0 / jj-lib 0.42.0
  and the landed split surface; leave §5/§12 as-is (already accurate).

**Acceptance:** no remaining "0.8.0" / "0.38" / "M2 = 0.3.0" status claims; headers agree with
`pyproject.toml` + a live import. **Source is not touched** (docs only).

**Risks:** none beyond keeping the diff docs-only.

---

## Step 3 — Two-revset `diff(from, to)` (CONCEPT §12 "Later")

**Why:** the single genuine *read-surface* gap. Today `RepoView.diff(revset)` / `diff_stat(revset)`
take one revset (the commit vs its parent — `repo_view.py:84,92`). jj supports `diff --from A --to B`
(arbitrary tree-to-tree). Consumers that want "what changed between two lanes / two ops" can't express
it without `run_jj`. Ask gitman whether it needs this before building (pull-driven).

**Deliverables / files:** `src/repo_view.rs` (a tree-diff between two resolved revs, reusing the
existing `ContentDiff::by_line` decomposition so `Hunk`/`HunkLine` shapes stay identical);
`python/pyjutsu/repo_view.py` + `workspace.py` (an overload / `from_`,`to` kwargs on `diff`/
`diff_stat`); `_pyjutsu.pyi`; `docs/PYJUTSU_CONCEPT.md` (move from §12 "Later" to shipped);
`tests/test_diff.py` / `test_diff_stat.py` (differential vs `jj diff --from --to`).

**Acceptance:** `ws.diff(from_="A", to="B")` matches `jj diff --from A --to B` (parity test style of
`test_rewrite.py`); single-revset form unchanged; green.

**Risks:** API shape (overload vs new method) is a compatibility decision — pick one and pin it in the
docstring. Keep the model shapes identical to the single-revset path so consumers reuse renderers.

---

## Step 4 — Optional: close the 0.7.0 code-review test-coverage gaps (polish)

**Why:** the review (`09-…/CODE_REVIEW.md` §7) flagged that although the **H1 slot-leak fix landed**
(`transaction.rs:797→803`), **no test forces `commit` to fail** to prove a subsequent transaction can
still open; there's no L1 concurrency test; `run_jj` error branches are thinly covered. These are
correctness *proofs*, not features — worth it before the next tag if a test seam is cheap.

**Deliverables / files:** a failure-injection seam if needed (per the review's "Testability" note) +
`tests/test_transaction.py` (failed-commit → slot released → next `ws.transaction` opens);
`tests/test_run_jj.py` (binary-not-found, `check=False` non-zero, `OSError`).

**Acceptance:** the slot-leak regression test **fails if `release_slot` is moved back** after the
fallible calls; `run_jj` error branches covered; green.

**Risks:** injecting a `commit` failure cleanly may need a small test-only hook — keep it out of the
public surface. Low value if effort balloons; drop rather than gold-plate.

---

## Step 5 — Deferred, only on explicit demand (do NOT start speculatively)

Track but don't build without a concrete consumer asking:
- **Word/inline (color-words) diff** — CONCEPT §12; needs engine-side word diff (CLI-options §5 flags
  it as real FFI work). No consumer pull yet.
- **A thin `pyjutsu-cli`** — proj 08 recommends **Option B** (separate package, no new FFI, ~one
  focused milestone; `--format=json` as the honest edge over `jj`). Its own project, not core work;
  the import-only core must never import it.
- **Native async facade** — explicit **non-goal** (jj's `!Send` tx model; `to_thread` suffices). Do
  not build; leave the README note as the answer.
- **`import_tags=False` adopt preset** (proj 10 §P2 option B) — only if a second consumer hits the
  off-main-tag-as-head surprise.

**Acceptance for this step:** none — it's a watchlist. Revisit when a consumer (gitman first) files a
concrete need.
