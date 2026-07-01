# ADR-009: Ambient module response requests

## Decision

Modules may set `ModuleContext.request_response`, but only the runtime may open a
listening window and generate or send a reply. `ambient_chat` uses this contract
after a persisted per-channel random line threshold is reached.

The module counts only channel messages for which runtime response is allowed.
When it requests an ambient response, it atomically resets its counter and stores
the next random threshold, preventing multiple users from opening parallel
ambient windows while the listening timer is active. Addressed replies also reset
the counter through `after_response`.

## Alternatives considered

- Let modules call the LLM and IRC client directly. This bypasses listening
  windows, ignore enforcement, sanitization, and cooldowns.
- Keep counters only in module objects. Restarts would reroll timing and lose
  canonical runtime progress.
- Leave a reached threshold active until sending completes. Messages from several
  users could request several simultaneous ambient replies.

## Reason chosen

The request contract keeps optional behavior modular while preserving runtime
authority and deterministic, inspectable state.

## Tradeoffs

A failed ambient generation still consumes that threshold and advances to the
next one. This avoids retry storms at the cost of occasionally skipping an
ambient contribution.
