# Bug Fixes Applied

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
