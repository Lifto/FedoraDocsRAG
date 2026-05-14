# Fix Groundhog Day Bug & Unreachable Repo Handling

## TL;DR

> **Quick Summary**: Fix two related bugs that prevent the build skip-path from ever reaching the fast "no changes" exit: (1) manifest not saved when content hash gate fires, (2) permanently-unreachable repos always triggering Gate 1/Gate 2 as "new."
> 
> **Deliverables**:
> - `build.py` — 3 edits: include failed repos in manifest, tolerate permanently-unreachable repos in Gate 2, save manifest on content hash skip path
> - `.github/workflows/build.yml` — 1 new step: upload manifest to existing release on skip path
> 
> **Estimated Effort**: Quick
> **Parallel Execution**: NO — sequential (single task, single file pair)
> **Critical Path**: Edit build.py → Edit build.yml → Push → Trigger CI

---

## Context

### Original Request
Fix two bugs preventing FedoraDocsRAG builds from reaching the fast "no changes detected" exit:
1. `save_manifest()` is never called on the content hash skip path (line 918-919), so the manifest stays stale and Gate 1/Gate 2 re-trigger every build.
2. `council_community-architecture` always fails to clone (unreachable repo), so it never appears in the manifest, causing Gate 1 to flag it as "new" every build — even when nothing has changed.

### Research Findings
- `build.py:201-261` — `check_repos_changed()`: Gate 1 compares repo URLs in site.yml against manifest URLs. Gate 2 runs `ls-remote` for each repo.
- `build.py:874-880` — `repos_shas` dict only includes repos where `get_repo_head_sha()` returned a value. Failed clones are excluded.
- `build.py:911-919` — Content hash gate does `return 0` without calling `save_manifest()`.
- `build.py:974` — `save_manifest()` is only called on the full rebuild path.
- `.github/workflows/build.yml:63-76` — Release step gated on `hashFiles('dist/fedora-docs.sql') != ''`, so skip-path builds never publish the manifest.
- `council_community-architecture` repo has been unreachable since at least May 2026. Both `git clone` and `git ls-remote` fail for it.

---

## Work Objectives

### Core Objective
Make the fast path ("No changes detected. Skipping build.") actually reachable on subsequent builds when no documentation content has changed.

### Concrete Deliverables
- `build.py` with three targeted edits
- `.github/workflows/build.yml` with one new step

### Definition of Done
- [ ] CI build triggered after push completes in <60 seconds (no-change path)
- [ ] `council_community-architecture` does NOT trigger Gate 1 as "new"
- [ ] Manifest is saved and published even on skip path

### Must Have
- Failed/unreachable repos recorded in manifest with empty SHA
- Gate 2 tolerates repos that were unreachable before and still are
- `save_manifest()` called before `return 0` on content hash skip path
- Manifest uploaded to existing release when no SQL dump produced

### Must NOT Have (Guardrails)
- Do NOT change fail-safe behavior for repos that were previously reachable (if a known-good repo suddenly can't be reached, that IS a trigger)
- Do NOT remove the content hash gate or change its logic
- Do NOT modify Gate 1 URL-set-diff logic (it correctly detects genuinely new repos)
- Do NOT add any new dependencies

---

## Verification Strategy

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed. No exceptions.

### Test Decision
- **Infrastructure exists**: NO (no test framework in this repo)
- **Automated tests**: None
- **Framework**: N/A

### QA Policy
Verification via CI build log analysis. Evidence saved to `.sisyphus/evidence/`.

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Single task — all edits are in 2 files, tightly coupled):
└── Task 1: Apply all 4 edits to build.py + build.yml [quick]

Wave FINAL (Verification):
└── Task F1: Trigger CI build, verify fast-path behavior from logs
```

### Agent Dispatch Summary

- **1**: **1** — T1 → `quick`
- **FINAL**: **1** — F1 → `quick`

---

## TODOs

- [x] 1. Fix manifest skip-path and unreachable repo handling

  **What to do**:

  **Edit A — Include failed repos in manifest** (`build.py` lines 874-880):

  Current code:
  ```python
  repos_shas = {}
  for url in repos:
      parts = url.rstrip("/").replace(".git", "").split("/")
      name = f"{parts[-2]}_{parts[-1]}" if len(parts) >= 2 else parts[-1]
      sha = get_repo_head_sha(WORK_DIR / name)
      if sha:
          repos_shas[url] = sha
  ```

  Change line 879-880 from:
  ```python
      if sha:
          repos_shas[url] = sha
  ```
  To:
  ```python
      repos_shas[url] = sha or ""
  ```

  This records ALL repo URLs in `repos_shas` (and thus in the manifest). Repos that failed to clone get empty string as SHA. Gate 1 will no longer flag them as "new."

  **Edit B — Tolerate permanently-unreachable repos in Gate 2** (`build.py` lines 220-250):

  In `check_repos_changed()`, three error paths need updating. When `ls-remote` fails for a repo whose manifest SHA is already empty, that's not a change — it was unreachable before and still is. Only trigger rebuild if the repo was previously reachable (non-empty SHA).

  At lines 230-234, change:
  ```python
              if result.returncode != 0:
                  print(
                      f"[gate2] Warning: ls-remote failed for {url} (exit {result.returncode}). Treating as changed."
                  )
                  return True
  ```
  To:
  ```python
              if result.returncode != 0:
                  if not manifest_sha:
                      print(f"[gate2] {url} still unreachable (was unreachable before). Skipping.")
                      continue
                  print(
                      f"[gate2] Warning: ls-remote failed for {url} (exit {result.returncode}). Treating as changed."
                  )
                  return True
  ```

  At lines 245-247, change:
  ```python
          except subprocess.TimeoutExpired:
              print(f"[gate2] Timeout for {url}. Treating as changed.")
              return True
  ```
  To:
  ```python
          except subprocess.TimeoutExpired:
              if not manifest_sha:
                  print(f"[gate2] {url} timed out (was unreachable before). Skipping.")
                  continue
              print(f"[gate2] Timeout for {url}. Treating as changed.")
              return True
  ```

  At lines 248-250, change:
  ```python
          except Exception as e:
              print(f"[gate2] Error checking {url}: {e}. Treating as changed.")
              return True
  ```
  To:
  ```python
          except Exception as e:
              if not manifest_sha:
                  print(f"[gate2] {url} error (was unreachable before): {e}. Skipping.")
                  continue
              print(f"[gate2] Error checking {url}: {e}. Treating as changed.")
              return True
  ```

  **Edit C — Save manifest on content hash skip path** (`build.py` lines 918-919):

  Change:
  ```python
          print("Content hash unchanged despite SHA changes — skipping rebuild")
          return 0
  ```
  To:
  ```python
          print("Content hash unchanged despite SHA changes — skipping rebuild")
          save_manifest(site_sha, repos_shas, content_hash, count)
          return 0
  ```

  **Edit D — Upload manifest on skip path in CI** (`.github/workflows/build.yml`):

  After the existing "Publish GitHub Release" step (line 76), add a new step:
  ```yaml
      - name: Upload manifest to latest release
        if: hashFiles('dist/manifest.json') != '' && hashFiles('dist/fedora-docs.sql') == ''
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          tag=$(gh release view --json tagName -q .tagName 2>/dev/null)
          if [ -n "$tag" ]; then
            gh release upload "$tag" dist/manifest.json --clobber
            echo "Uploaded manifest.json to release $tag"
          else
            echo "No existing release found, skipping manifest upload"
          fi
  ```

  This fires ONLY when the build produced a manifest (content hash skip path saved it) but no SQL dump (no full rebuild). It updates the manifest in the existing latest release using `--clobber` to replace the old one.

  **After all edits**: Commit with message `fix: save manifest on skip path and handle unreachable repos` and push to main. Then trigger a CI build (`gh workflow run build.yml`) to verify.

  **Must NOT do**:
  - Do not change the content hash gate logic itself
  - Do not remove fail-safe behavior for previously-reachable repos
  - Do not modify Gate 1 URL-set-diff logic

  **Recommended Agent Profile**:
  - **Category**: `quick`
    - Reason: 4 targeted edits across 2 files, clear line-by-line instructions, no design decisions
  - **Skills**: []
    - No special skills needed — straightforward file edits
  - **Skills Evaluated but Omitted**:
    - `git-master`: Simple commit+push, not needed

  **Parallelization**:
  - **Can Run In Parallel**: NO (single task)
  - **Parallel Group**: Wave 1
  - **Blocks**: F1 (verification)
  - **Blocked By**: None

  **References**:

  **Pattern References**:
  - `build.py:264-297` — `save_manifest()` function signature and implementation
  - `build.py:201-261` — `check_repos_changed()` with Gate 1 and Gate 2 logic
  - `build.py:874-880` — `repos_shas` dict construction loop
  - `build.py:911-919` — Content hash gate and early return
  - `build.py:974` — Existing `save_manifest()` call on full rebuild path (for reference on args)
  - `.github/workflows/build.yml:62-76` — Existing release step (pattern for new step)

  **WHY Each Reference Matters**:
  - `save_manifest()` at line 264: executor needs the function signature to write the call correctly — args are `(site_sha, repos_shas, content_hash, count)`
  - `check_repos_changed()` at line 201: executor must understand the Gate 1 → Gate 2 flow to know exactly where to insert the `if not manifest_sha` guards
  - `repos_shas` at line 874: executor must see the current `if sha:` guard to know what to change
  - Line 974: shows the existing `save_manifest()` call pattern to copy

  **Acceptance Criteria**:

  **QA Scenarios (MANDATORY):**

  ```
  Scenario: Verify all edits applied correctly
    Tool: Bash (grep + read)
    Preconditions: Edits applied, not yet pushed
    Steps:
      1. grep "repos_shas\[url\] = sha or" build.py — should match line ~879
      2. grep "still unreachable" build.py — should find 1 match (ls-remote failure path)
      3. grep "timed out.*was unreachable" build.py — should find 1 match
      4. grep "error.*was unreachable" build.py — should find 1 match
      5. grep "save_manifest" build.py — should find 3 matches (definition, skip path, full rebuild)
      6. grep "Upload manifest to latest release" .github/workflows/build.yml — should find 1 match
      7. grep "clobber" .github/workflows/build.yml — should find 1 match
    Expected Result: All 7 greps return matches
    Failure Indicators: Any grep returns 0 matches
    Evidence: .sisyphus/evidence/task-1-edits-verified.txt

  Scenario: Push and trigger CI build
    Tool: Bash (git + gh)
    Preconditions: All edits committed
    Steps:
      1. git push origin main
      2. gh workflow run build.yml --repo Lifto/FedoraDocsRAG
      3. Wait for build to complete: gh run watch $(gh run list -w build.yml -L 1 --json databaseId -q '.[0].databaseId') --repo Lifto/FedoraDocsRAG
      4. Fetch build logs: gh run view <id> --log --repo Lifto/FedoraDocsRAG
      5. Check logs for: "[gate2] ... still unreachable" (council_community-architecture handled gracefully)
      6. Check logs for either "No changes detected. Skipping build." OR "Content hash unchanged" with "[manifest] Saved manifest"
    Expected Result: Build completes in <2 minutes. Unreachable repo logged as skipped, not as trigger.
    Failure Indicators: Build takes >5 minutes, or Gate 1 flags "New repos detected", or Gate 2 says "Treating as changed" for council_community-architecture
    Evidence: .sisyphus/evidence/task-1-ci-build.txt

  Scenario: Verify fast-path on second consecutive build
    Tool: Bash (gh)
    Preconditions: First build after fix completed successfully
    Steps:
      1. Trigger another build: gh workflow run build.yml --repo Lifto/FedoraDocsRAG
      2. Wait for completion
      3. Check logs for "No changes detected. Skipping build." — this is the FAST path
      4. Verify build duration < 60 seconds
    Expected Result: "No changes detected. Skipping build." appears in logs. Build completes in ~30 seconds.
    Failure Indicators: Build enters clone/Antora/extract steps, or takes >2 minutes
    Evidence: .sisyphus/evidence/task-1-fast-path.txt
  ```

  **Commit**: YES
  - Message: `fix: save manifest on skip path and handle unreachable repos`
  - Files: `build.py`, `.github/workflows/build.yml`
  - Pre-commit: N/A

---

## Final Verification Wave

- [x] F1. **CI Build Verification** — `quick`
  Trigger CI build and verify from logs that: (1) unreachable repos are skipped gracefully, (2) manifest is saved on skip path, (3) second consecutive build reaches fast "No changes detected" exit in <60 seconds.

---

## Commit Strategy

- **T1**: `fix: save manifest on skip path and handle unreachable repos` — build.py, .github/workflows/build.yml

---

## Success Criteria

### Verification Commands
```bash
# After two consecutive CI builds:
# Build 1: should complete with manifest saved (either full rebuild or skip path)
# Build 2: should exit with "No changes detected. Skipping build." in ~30 seconds
```

### Final Checklist
- [ ] Unreachable repos don't trigger Gate 1 as "new"
- [ ] Unreachable repos don't trigger Gate 2 as "changed"
- [ ] Previously-reachable repos that become unreachable STILL trigger (fail-safe preserved)
- [ ] `save_manifest()` called on content hash skip path
- [ ] Manifest uploaded to release on skip path
- [ ] Fast path reachable on second consecutive no-change build
