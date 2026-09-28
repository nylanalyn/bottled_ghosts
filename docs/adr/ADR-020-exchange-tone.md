# ADR-020: Exchange tone from the replying model

## Decision

When the moods or affinity module is enabled, the reply prompt asks the model
to end an addressed reply with one hidden line, `[tone: nick=label, ...]`,
rating how each person who addressed the Bottle treated it: `warm`, `neutral`,
`cold`, or `hostile`. The runtime strips every tone tag from every response,
requested or not, before modules or IRC see it, and maps rated nicks to the
user IDs in the listening window.

Affinity now changes after the exchange instead of on each addressed message:
warm raises warmth, neutral adds slight familiarity, cold and hostile lower it,
with diminishing returns toward the end it moves to. Moods shift valence by
the average rating and irritability by the harshest one, scaled by a
per-Bottle `tone_sensitivity` (0 disables it), and record a `tone` event.
Migration 038 rebuilds `mood_state` to allow that event.

## Alternatives considered

- A separate classification call per exchange doubles LLM calls, the cost
  that already keeps per-reply sediment extraction off by default.
- Lexical sentiment misreads IRC sarcasm and banter (ADR-012).
- Rating whole-room sentiment would let bystanders change how the Bottle
  feels about someone who never spoke to it.

## Reason chosen

The replying model already reads the full exchange with the character's
context, so it is the best-placed judge and adds no call. Code, not the
prompt, guarantees the tag cannot reach IRC. Ratings only move slow, bounded
state that remains inspectable in `user_affinity` and `mood_state`.

## Tradeoffs

A speaker can try to talk the model into rating them warmly; the effect is a
small, bounded change to their own score and is limited by the diminishing
returns. Sustained genuine hostility can now drive irritability to its ceiling
and trigger a mood room break, which is intended behavior but is also a way
for someone abusive to push a Bottle out of a room for thirty minutes. A model
that ignores the instruction produces no rating (logged), and affinity then
stops changing rather than guessing. Ambient contributions are not rated.
