# ADR-013: Runtime-enforced mood room breaks

## Decision

When the optional moods module reaches irritability `1.0` from a channel
message, it requests a 30-minute break. The IRC runtime validates that the
channel is configured, records the break in SQLite, prompts the Bottle for one
short farewell, then sends `PART` and suppresses responses there. The farewell
must acknowledge annoyance and a roughly 30-minute step-away without insulting
or naming people; a fixed fallback is used if generation fails. At the recorded
due time the runtime restores the mood's configured baselines with zero
interaction heat, records completion, and sends `JOIN`.

## Alternatives considered

- Prompting the model to leave when annoyed would make a safety and lifecycle
  decision probabilistic.
- Disconnecting the whole IRC client would leave unrelated rooms and make the
  return timing depend on reconnect backoff.
- An in-memory timer would lose the break across a runtime restart.

## Reason chosen

The runtime already owns IRC membership. Persisting the per-channel break
before `PART` keeps the behavior recoverable and inspectable, including across
reconnects. The mood module stays character-neutral: it only detects its
configured numeric ceiling and supplies its profile baselines.

## Tradeoffs

Mood is global per Bottle while breaks are per channel. A completed break resets
the global mood before rejoining that channel. A runtime restart during a break
does not join that channel until its persisted due time, but the exact JOIN may
occur slightly after the deadline while reconnecting.
