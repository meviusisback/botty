# Bug Analysis: Botty Scroll Flicker & OpenCode Session Error

## Bug 1: Scroll-to-bottom flickering

### Root Cause
Multiple competing scroll triggers create a feedback loop when the user clicks the scroll-to-bottom button:

1. `scrollChatToEnd()` is called → sets `autoScrollPinned = true`
2. `positionViewAtEnd()` executes → contentHeight may change slightly
3. `onContentHeightChanged` fires → calls `positionViewAtEnd()` again
4. This triggers another layout pass → contentHeight changes again
5. `scrollStabilizeTimer` fires after 80ms → another scroll attempt

The cycle repeats until the layout stabilizes, causing visible flickering.

Additionally, `historyFileView.onFileChanged` can trigger `scrollChatToEnd()` while the user is already scrolling to the bottom, causing the scroll position to jump.

### Fix Strategy
1. Add `scrollInProgress` guard flag to prevent re-entrant scroll calls
2. Debounce `scrollChatToEnd()` calls
3. Only trigger auto-scroll from file changes if user was already at the bottom
4. Increase stabilize timer to allow layout to settle

## Bug 2: OpenCode MissingSessionID

### Root Cause  
The error `Error code: 400 - {'type': 'error', 'error': {'type': 'MissingSessionID', 'message': 'Error from provider (Console Go): Request is missing x-opencode-session and cannot be routed efficiently.'}}` indicates the OpenCode Go provider requires a session header that Hermes isn't providing.

This happens when the Hermes engine tries to use the `opencode-go` provider - the provider's API expects a session identifier for request routing, but Hermes doesn't know to include it.

### Fix Strategy
The issue is in how the Hermes CLI is invoked. When using `opencode-go` as the provider, we may need to either:
1. Check if Hermes has a way to pass session headers
2. Switch to a different provider that doesn't require session headers
3. Report this as a known limitation in the model selection UI
