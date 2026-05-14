# ls-remote retry for flaky repos

## TL;DR

> **Quick Summary**: Add retry logic (3 attempts, 3s/6s backoff) to `git ls-remote` in Gate 2 so intermittently-unreachable repos don't trigger unnecessary full rebuilds.
> 
> **Deliverables**:
> - Updated `check_repos_changed()` with retry loop
> 
> **Estimated Effort**: Quick
> **Parallel Execution**: NO — single task
> **Critical Path**: T1 → commit → CI verify

---

## Context

### Original Request
`forge.fedoraproject.org/infra/docs.git` intermittently fails `ls-remote` in CI, triggering Gate 2's fail-safe ("Treating as changed") which forces a full clone+Antora+extract cycle (~4 min) even though no content actually changed. A simple retry would resolve transient network blips.

### Evidence
Build run 25573455915 — `forge.fedoraproject.org` ls-remote failed once, triggered full rebuild path, content hash gate caught it (no actual changes).

---

## Work Objectives

### Core Objective
Retry flaky `ls-remote` calls before declaring a repo "changed."

### Must Have
- 3 total attempts per repo (1 original + 2 retries)
- Backoff: 3 seconds after first failure, 6 seconds after second failure
- Retry on: non-zero exit code, timeout, and exceptions
- After all retries exhausted: existing fail-safe behavior preserved ("Treating as changed" / `return True`)
- Log each retry attempt so CI logs show what happened

### Must NOT Have
- No retries for repos with empty manifest SHA (permanently unreachable) — those already skip via `if not manifest_sha: continue`
- No retry on successful ls-remote that returns empty HEAD (that's a data problem, not transient)
- No changes to timeout value (keep 30s per attempt)
- No new dependencies — use `time.sleep()` from stdlib

---

## Verification Strategy

> **ZERO HUMAN INTERVENTION** — ALL verification is agent-executed.

### Test Decision
- **Infrastructure exists**: NO (no unit tests in this project)
- **Automated tests**: None
- **QA**: CI build verification

---

## Execution Strategy

Single task, no parallelism needed.

---

## TODOs

- [x] 1. Add retry loop to ls-remote in check_repos_changed()

  **What to do**:
  - Add `import time` if not already present
  - Wrap the `subprocess.run(["git", "ls-remote", ...])` call (lines 224-258 of `build.py`) in a `for attempt in range(3)` loop
  - On first attempt failure: log `[gate2] ls-remote failed for {url}, retrying in 3s (attempt 1/3)...`, sleep 3s
  - On second attempt failure: log `[gate2] ls-remote failed for {url}, retrying in 6s (attempt 2/3)...`, sleep 6s
  - On third attempt failure: existing behavior (check `manifest_sha`, either skip or `return True`)
  - Same pattern for `TimeoutExpired` and generic `Exception`
  - On success at any attempt: `break` out of retry loop, continue with SHA comparison
  - The `if not manifest_sha: continue` guards for permanently-unreachable repos should remain OUTSIDE the retry loop (check before retrying — no point retrying a repo that was never reachable)

  **Must NOT do**:
  - Don't change the 30s timeout per attempt
  - Don't retry for empty HEAD ref (line 240-242) — that's not transient
  - Don't retry permanently-unreachable repos (empty manifest SHA)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: `[]`

  **Parallelization**:
  - **Can Run In Parallel**: NO
  - **Parallel Group**: Sequential (single task)
  - **Blocks**: Nothing
  - **Blocked By**: None

  **References**:

  **Pattern References**:
  - `build.py:220-270` — Current `check_repos_changed()` Gate 2 loop. The retry wraps the `subprocess.run` call and its error handling within each iteration of the `for url in repo_urls` loop.

  **Key lines**:
  - `build.py:222` — `manifest_sha = manifest["content_repos"].get(url, "")` — check this BEFORE retry loop
  - `build.py:224-228` — `subprocess.run(["git", "ls-remote", url, "HEAD"], ...)` — the call to retry
  - `build.py:230-237` — Non-zero exit handling (retry this)
  - `build.py:248-253` — TimeoutExpired handling (retry this)
  - `build.py:254-259` — Generic exception handling (retry this)
  - `build.py:239-246` — Success path with SHA comparison (break out of retry on success)

  **Acceptance Criteria**:

  **QA Scenarios (MANDATORY):**

  ```
  Scenario: Verify retry logic structure
    Tool: Bash (grep)
    Steps:
      1. grep -n "attempt" build.py — confirm retry loop exists
      2. grep -n "time.sleep" build.py — confirm backoff calls with 3 and 6
      3. grep -n "retrying in" build.py — confirm retry log messages
    Expected Result: Retry loop with 3 attempts, sleep(3) and sleep(6), log messages present
    Evidence: .sisyphus/evidence/task-1-retry-structure.txt

  Scenario: Verify permanently-unreachable skip is outside retry
    Tool: Bash (grep)
    Steps:
      1. Read the function, confirm `if not manifest_sha: continue` appears BEFORE the retry loop
    Expected Result: Permanently-unreachable repos skip without retrying
    Evidence: .sisyphus/evidence/task-1-skip-check.txt
  ```

  **Commit**: YES
  - Message: `fix: add ls-remote retry (3s/6s backoff) for flaky repos`
  - Files: `build.py`

---

## Final Verification Wave

- [x] F1. **CI Build Verification** — Trigger CI build, confirm it passes. Check logs for retry-related messages (or absence if all repos reachable). Verify no regressions.

---

## Commit Strategy

- **T1**: `fix: add ls-remote retry (3s/6s backoff) for flaky repos` — build.py

---

## Success Criteria

### Final Checklist
- [ ] Retry loop with 3 attempts, backoff 3s/6s
- [ ] Permanently-unreachable repos still skip without retry
- [ ] Fail-safe preserved after all retries exhausted
- [ ] CI build passes
