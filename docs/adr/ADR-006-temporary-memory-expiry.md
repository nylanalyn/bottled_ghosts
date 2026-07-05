# ADR-006: Temporary memory expiry

## Decision

Approved `temporary_state` memories expire 24 hours after approval. Expiration is
represented by `user_memories.expires_at`; expired rows remain stored for audit,
but active-memory views and prompt retrieval exclude them. Maintenance and audit
tools may explicitly request expired rows when historical inspection is needed.

Changing a durable memory to `temporary_state` starts a new 24-hour lifetime.
Changing a temporary memory to another category clears its expiry. Both values
are recorded in the append-only audit event.

## Alternatives considered

- Store temporary state permanently. This makes short-lived context stale and
  misleading.
- Delete expired rows. This loses provenance and makes behavior harder to audit.
- Run a hidden cleanup scheduler. This conflicts with explicit operator-controlled
  background work.
- Ask the extractor to choose arbitrary expiry times. Model-provided lifetimes are
  difficult to validate and make behavior less predictable.

## Reason chosen

A fixed lifetime is deterministic and requires no scheduler or background state
mutation. Keeping expired rows preserves the complete operator-visible history.

## Tradeoffs

Twenty-four hours is a coarse policy and may not suit every temporary fact. A
future explicit operator control can extend or shorten expiry without changing
the retrieval rule.
