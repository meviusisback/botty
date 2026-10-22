# Bug Fixes Applied

## 2026-10-22 (branch feat/context): per-turn context duplication + dead auto-compaction

### 1. The system directive was re-sent on every message ✅ FIXED

`ask()` prefixed `[SYSTEM DIRECTIVE] <BOTTY_AGENT_DIRECTIVE>` to **every** user
turn, even though `hermes chat --continue botty-widget` keeps the session
server-side — the directive from turn 1 is still in the model's context on turn
N. Measured on the live session: 31 user messages, 30 of them carrying the
directive (1476 chars each) → ~48% of every prompt was the same repeated text,
and the last request carried ~30 copies of it.

Fix: the directive is now a **session preamble**. `hermes_session_has_preamble()`
reads the live session's last 60 user turns from `state.db`; the directive (and
the continuity bridge) are included only when that session does not carry them
yet — a brand-new session, or one reset by compaction/Clear. Every turn still
carries a short `[MODE: SANDBOXED …]` / `[MODE: UNRESTRICTED RUN …]` line,
because the write policy can flip between turns and a stale copy in the history
must not be the last word. omp/claude/codex are unchanged: they have no
server-side session, so they keep receiving the full context every run.

Verified with a real `hermes` run on an isolated session: turn 1 = 2801 chars
(directive sent), turn 2 = 373 chars (directive skipped), both answered
correctly. Doubt (missing/corrupt DB, no session) falls back to sending the
directive — losing it is worse than a few hundred tokens.

### 2. Auto-compaction never ran ✅ FIXED

The trigger lived inside `ask()` and called `distill_and_compact_session()`
there. That function refuses to prune while an ask is live (`_ask_live()` reads
the PID file the running ask itself created), so it returned "Botty is
processing a request" on **every** turn and the result was ignored. Effect: no
compaction since 2026-09-12 while history reached 62 messages (threshold: 14)
and the Hermes session grew to 339 messages / ~96k tokens re-sent per turn.

Fix: the decision stays in `ask()`, execution moved to the `finally` block,
after the ask lock is released, as a detached child (`botty_backend.py compact`
— the same path the UI's Compact button uses). Answer latency is unchanged.

### 3. Tests were mutating the live profile ✅ FIXED

`test_security.py` called `distill_and_compact_session()` without isolating
`HERMES_STATE_DB`, so running the test suite renamed the user's real
`botty-widget` session to `archived-botty-widget-*` (the widget then looks like
it lost its chat history). It also called `clear_history()` with the real bridge
path (deleting it) and rebuilt the real `vault.enc` from the temp fixtures
(71 KB -> 1 KB), while `test_compaction.py` appended its compaction logs to the
real `botty.log`.

All three test files now isolate `HERMES_STATE_DB`, `HERMES_SKILLS_DIR`,
`HERMES_MEMORY_DIR`, `BOTTY_LOG_FILE`, `CONTEXT_BRIDGE_FILE`, `VAULT_FILE` and
`APPROVED_STAGING_DIR`. Verified with a sha256 + line-count fingerprint of the
live data dir, the vault, the bridge, the staging dir and the session table taken
before and after a full suite run: identical.

`test_context_preamble.py` (new, 16 tests) covers preamble detection, prompt
composition, the mode line, the auto-compaction trigger and bridge cleanup on
Clear.

### 4. Every reply started with `↪ restored workspace dir: …` ✅ FIXED

`hermes chat --continue` restores the resumed session's recorded working
directory and prints `↪ restored workspace dir: <path>` on stdout. Botty treats
stdout as the answer, so that CLI notice became the first line of every reply in
the widget and of every entry in `history.json`.

Fix: `strip_cli_notices()` runs inside `strip_reasoning()` (the single funnel for
response text) and drops exactly that marker line — anchored to
`restored workspace dir:` so arrows used as prose survive. Stored messages were
cleaned in place (7 of 64, backup kept); nothing else changed.

Verified with a real `hermes` run: answered turn 2 from a different working
directory (the condition that triggers the restore) and the notice no longer
reaches the saved answer.

### 5. Fix round after the security review (3 reviewers, 2 blocking verdicts)

The first review round rejected the change. What it found, and what changed:

1. **The detached child hijacked the shared status** (blocking). `distill_and_compact_session()`
   set `state="working"` / "Distilling memory & compacting…", which the widget reads as "an ask
   is in flight": it blocks the input field and shows "Botty thinking…" for the whole
   distillation. → The child now runs `compact --background` and touches neither the status nor
   `last_answer`.
2. **No compaction-level mutex** (blocking): the auto-spawn, the UI button and a manual
   `compact` could all run at once, each burning a full agent call, and — worse — the archive
   append ran BEFORE the history re-validation, so a losing compactor still appended its
   messages (probe: 3 concurrent runs archived 12 messages 36 times). → `COMPACT_LOCK_FILE`
   (`compacting.pid`, O_EXCL + PID liveness) is claimed before any LLM work and released in a
   `finally`; the archive append now happens inside the validated lock region, and proposal
   staging after the commit (flock is not re-entrant, staging takes it itself).
3. **The preamble check was fail-OPEN under Hermes' own compression** (blocking): Hermes
   compresses a long session in place (`active = 0` soft archive) and builds the live prompt
   from `active = 1` rows, so a marker in an archived row would suppress the directive forever.
   → `_session_messages_filter()` probes the schema and adds `COALESCE(active,1)=1` /
   `COALESCE(compacted,0)=0`; the read is `mode=ro` with a 1 s timeout on the answer path.
4. **Silent child + no backoff**: the parent logged "started" before the child did anything and
   the child's output went to /dev/null. → the child's stdout/stderr go to `compaction.log`,
   every abort path logs a `COMPACTION REFUSED/ABORTED/DEFERRED` line, and a 600 s cooldown
   (overridden when the history grew) stops a repeatedly failing run from calling the model
   every turn.
5. **Session reset could detach a live ask**: `_ask_live()` was checked once, ~90 s before the
   rename. → the bridge write and `reset_hermes_session()` are skipped (and logged) if an ask
   went live during the distillation.
6. Cheap ones also in: the mode line now says it supersedes any earlier MODE/sandbox
   instruction; the bridge summary/tail are capped (2000 / 6000 chars); all three suites isolate
   `COMPACT_LOCK_FILE`, `COMPACTION_*`, `HISTORY_LOCK_FILE`, `VOICE_PID_FILE`, `SCREENSHOT_DIR`
   (a fingerprint of the live dir is byte-identical across a run).

Declined: the "bridge is re-injected unfenced" finding is contradicted by the code — `ask()`
already wraps it in a `[UNTRUSTED DATA — reference only…]` fence (only the cap was missing).

Guard sweep: 8 mutations (mutex, both status skips, `active` filter, abort-before-side-effects,
`--background` in the child argv, cooldown, notice stripping) — **8/8 CAUGHT** by a named test.


### 6. Second fix round (concurrency reviewer: 4 medium findings, all probe-backed)

1. **The compactor mutex was stealable.** `O_EXCL` create and the PID write are two
   syscalls and an unparsable file counted as stale, so a second compactor arriving in
   that gap took a lock the first one believed it held. → The compaction slot is now an
   **flock held open for the whole run**: the kernel releases it on process death (no
   stale-file reaping at all) and there is no window to steal.
2. **Release deleted the successor's claim.** `unlink()` ran unconditionally, so a late
   owner erased the new owner's file and a third run was admitted. → `_release_pid_file()`
   / `_release_compact_lock()` only release what is still ours.
3. **The 600 s cooldown could never fire.** `messages_at_attempt` was recorded with the
   count the same turn had already grown, so the "history grew" override was always true
   (probe: 3 turns → 3 spawns, 2 aborts, 0 skips). → It backs off on a consecutive-attempt
   counter that a successful compaction resets; history growth no longer bypasses it.
4. **A deferred session reset was never retried.** The prune committed, the reset was
   skipped because an ask was live, and nothing tried again: Hermes kept the whole
   inflated context while the UI said "compacted". → The reset is persisted
   (`reset_pending` + the bridge text) and applied at the start of the next turn, when no
   ask is live.

Also from this round: `_pid_file_live()` is side-effect free (it no longer unlinks from a
read path that runs inside the history flock); the CLI-notice stripper is fence-aware and
also drops Hermes' other stdout notice (`⚠ session's recorded dir is gone …`); the
preamble docstring no longer claims the profile dir is untouched (a `-shm` sidecar may
appear). Residual, accepted: a check-then-act window of a few milliseconds between the
"is an ask live?" check and the session rename.

Guard sweep after round 2: **17 mutations, 17/17 CAUGHT**.

## 2026-09-12 update: ALL scroll changes REVERTED

After 4 rounds of scroll patches the widget flickered constantly even idle —
worse than the reported bug. Per systematic-debugging Phase 4.5 (3+ failed
fixes = wrong approach), every Panel.qml scroll change was reverted to
upstream HEAD (verified: zero scroll-related diff vs HEAD). The remaining
Panel.qml diffs vs HEAD are pre-existing workspace dev work (provider-tab
preservation, set-model immediate apply, proposal buttons) untouched by this
session. Bug 2 backend handling (below) is kept — it is unrelated to the
flicker and only improves error paths.

## Bug 1: Scroll-to-bottom flickering ✅ FIXED (minimal fix)

### Root Cause
Two compounding causes in the original code:

1. **The scroll-to-bottom button never pinned.** Its handler called bare
   `chatListView.positionViewAtEnd()` without setting `autoScrollPinned = true`.
   In long threads, message delegates lay out asynchronously (Markdown
   rendering grows `contentHeight` after the first positioning), so the view
   landed mid-thread and nothing re-asserted the end → "stuck, flickers the
   last message, never reaches the bottom".
2. **Synchronous `contentY = maxY` yanks mid-layout.** `onContentHeightChanged`
   (and the old `Qt.callLater` double-position + 80ms timer) set `contentY`
   directly inside/around the layout pass that was still growing
   `contentHeight`. Each yank triggered another layout → another
   `onContentHeightChanged` → loop = visible flicker.

### Fix (Panel.qml only, no new properties)
- Button now calls `root.scrollChatToEnd()` (pins + positions + arms the
  stabilize timer) instead of bare `positionViewAtEnd()`.
- `scrollChatToEnd()`: pin + `positionViewAtEnd()` + restart stabilize timer.
  No manual `contentY`, no `Qt.callLater` double-position.
- Stabilize timer (120ms): single re-assert via `positionViewAtEnd()`, only
  `if (... && !chatListView.atYEnd)`.
- `onCountChanged` / `onContentHeightChanged`: while pinned, just
  `scrollStabilizeTimer.restart()` (coalesce rapid layout events into one
  deferred re-assert) instead of repositioning synchronously.
- History watchers (`historyFileView.onLoaded`, `historyProc`): only
  auto-scroll for new messages while pinned — never yank the user away from
  old messages they are reading up-thread.

### Reverted bad ideas from earlier iterations (all removed)
- `property int lastScrollTime / scrollCooldownEnd` + `Date.now()`: **32-bit
  int overflow** (`Date.now()` ≈ 1.75e12 > int max 2.1e9) → garbage
  debounce/cooldown that broke opening and scrolling. Gone.
- `-20px` bottom offset: broke `atYEnd` semantics (button never hid, pinned
  state confused, previous message hidden). Gone.
- `cacheBuffer: 400`: extra layout cost in exactly the long threads that
  flicker. Gone.
- Movement-handler guards: broke unpin semantics. Reverted to original.

## Bug 2: OpenCode MissingSessionID error ✅ HANDLED

Upstream Hermes PR #107021 ("send ephemeral x-opencode-session header on
one-shot requests") is still OPEN — the fix is in no released Hermes version
(user on v0.21.1, latest v0.21.2). Until it merges, `botty_backend.py`:
- detects `MissingSessionID`/`x-opencode-session` in `ask()` errors and shows
  actionable guidance (switch provider in Settings), and
- degrades gracefully in `distill_and_compact_session()` (fallback summary
  instead of failing compaction).

## Deploy (run on host, sandbox plugin dir is read-only)
```bash
cp /home/alberto/orca/workspaces/botty/Bug-fixing/Panel.qml ~/.config/omarchy/plugins/meviusisback.botty/Panel.qml
cp /home/alberto/orca/workspaces/botty/Bug-fixing/botty_backend.py ~/.config/omarchy/plugins/meviusisback.botty/botty_backend.py
omarchy restart shell
```

## Security hardening (2026-09-12, 4-track review + implementation) ✅

Review: 4 parallel read-only tracks (sandbox/authz, taint, logic/concurrency,
supply-chain), parent-verified all High/Medium claims. Supply track clean
(stdlib-only, no secrets, no tracked data files, no shell=True anywhere).

- **H1**: `add-memory --from-model` stages model-derived facts as pending
  proposals instead of writing live MEMORY.md. User-typed facts unchanged.
  Save Fact button passes `--from-model`. New `test_security.py` (10 tests).
- **H2**: sandboxed `ask()` on non-Hermes engines refused with guidance
  (approval gate is Hermes-only); lock file cleaned on refusal;
  `get-sandbox` reports `active_engine` + `sandbox_technically_enforced`.
- **M2**: `_synchronized` (reentrant flock) on history/status/proposals
  writers; mkstemp-unique tmp + atomic replace; uuid message/proposal ids;
  monotonic history `rev` (UI ignores stale polls); clear/compact refuse
  while ask live (stale-PID safe); compaction revalidates tail before prune.
  QML: `historyRev` guard + history refresh after compact/proposal review.
- **M1**: `inspect_file` denies credential paths (~/.ssh, ~/.gnupg, *.pem/
  *.key, *.env variants, /etc/shadow|gshadow) after canonicalization.
- **L2**: `redact_secrets` applied in `append_botty_log` + `set_status`
  last_* fields (display-only consumers).
- **L1/L4**: no change — QML Process has no stdin channel (documented
  accepted risk: local, transient); IPC surface mitigated via M1.
- Pre-existing test failure noted: `test_distill_and_compact_session`
  line 285 expects approval→live memory, but code stages inert — fails
  identically on pristine HEAD. Left untouched (needs product decision).

## Security round 3 (verification fixes) ✅

Re-review judged prior fixes: H1/H2-ask/M2-core/M1/L2/supply HOLD.
Fixed the residuals (21/21 `test_security.py` green; only pre-existing
`test_compaction` line-285 failure remains, identical on pristine HEAD):

- **H2b**: distill forces Hermes while sandboxed (compaction must not fail).
- **A2**: permission marker matched outside fenced code; approval card +
  banner copy warns to verify every path/command.
- **A3**: `--from-model` stages regardless of `--user` (routes user facts
  to `user_memories`); staging failures set error headlines (T7 surfacing).
- **A4**: `apply_proposal` re-validates schema; fixed stale help text.
- **T1/T2**: attach reads via lstat gate + `O_NOFOLLOW|O_NONBLOCK` fd +
  fstat re-verify; symlinks denied pre-resolve; 5MB cap.
- **T3**: denylist expanded (aws/azure/kube/docker/password-store/gcloud/
  keyrings/mozilla, sk/ppk/ovpn/age/vault/env, *secret*/*credential*/
  *passwd*/*private*, shadow/ssl/ssh dirs) + private-key content sniff.
- **T6**: redaction patterns added (gho_/github_pat_/glpat-/AKIA/
  sk-ant-/sk-proj-/AIza/xox/PEM-block/JWT-3seg).
- **T8/T10**: headline + situation-cmdline redaction; **T9**:
  `current_query.txt` owner-only; **T11**: user/system dedup sets split.
- **F1/F3**: `get_status`, stage/apply/reject fully decorated;
  **F2**: process-wide RLock; **F4/F6**: atomic O_EXCL ask acquisition,
  EPERM-means-live, single `_ask_live` helper; **F5**: revalidation
  re-read inside held lock with exact length+last-id match.
- Rejected as findings (verified): verbatim history/archive/bridge
  (by-design, 0600/0700), F5b empty-tail (proven no-op — to_compact empty
  returns before any write; locked as regression test), F2 mechanism
  (flock does serialize; RLock added anyway), vault/clipboard verbatim.

## Security round 4 (verification fixes) ✅

Re-review verdicts: distill-gate/H1-staging/apply-gate/M1/L2/supply HOLD.
Fixed the residuals (30/30 `test_security.py`, node suite clean):

- **P0 lock leak**: failed-spawn early return now unlinks LOCK_FILE
  (regression test; second ask no longer wedges).
- **A2**: fence-strip also strips to EOF on unclosed openers; blockquote
  and HTML-comment lines are dropped (not unquoted) before matching;
  Model.js fallback mirrors backend strip + padlock-only match; approval
  card/banner carry verify-before-approving copy. Residual by design:
  plain-prose quotes rely on user review.
- **Taint**: fd open/fstat/read wrapped with close-on-all-paths and
  fail-closed errors; fstat size used for reporting; selection, window
  titles, and per-tool titles redacted at collection; live dedup split
  by user/system store.
- **Logic**: distill manual section under RLock ordering; apply/reject/
  stage fully decorated; O_EXCL acquisition + EPERM-means-live + single
  `_ask_live`; revalidation re-read inside held lock with exact
  length+last-id match.
- **Tests**: +9 (lock cleanup, fence table + ask-level fenced negative,
  open/fstat failures, distill fallback, pattern near-miss, EPERM gate,
  empty-tail no-op); node permission-marker matrix (10 checks).
- Deliberately unchanged: verbatim history/archive/bridge (product
  requirement, 0600/0700); get_status SH/EX split (negligible contention);
  equal-length-different-content (no external writers in threat model);
  stale `test_compaction` line-285 expectation (product decision pending).
