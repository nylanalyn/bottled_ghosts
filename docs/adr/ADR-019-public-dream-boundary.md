# ADR-019: Public dream boundary

## Decision

Nightly dreams read only public-channel messages. Migration 036 adds
`summaries.public_safe`, defaulting to false for all existing summaries.
Only new dreams generated from public input are marked true. Reply prompts
and the followups module use only marked summaries. Historical summaries and
followups remain in SQLite for operator inspection.

The pending `temporary_state` backlog is handled by a separate, explicit
preview-first cleanup command. Applying it rejects old pending candidates and
writes an audit event for each one.

## Alternatives considered

- Keep private messages in dreams and rely on the model to avoid disclosing
  them. A prompt cannot enforce that boundary.
- Guess which old dreams were public-only. Their inputs are not recorded well
  enough to prove it, so we treat all as unverified.
- Delete historical summaries and temporary candidates. Keeping them permits
  inspection and preserves audit history.
- Automatically expire temporary candidates in the background. An explicit
  operator action makes the queue change visible and reviewable.

## Reason chosen

Dreams are reused in later replies, so their input must have a clear public
boundary. Defaulting historical rows to unsafe closes the migration gap.
Temporary candidates represent short-lived context and should not remain in
the review queue indefinitely.

## Tradeoffs

Old dream continuity disappears from reply prompts until fresh dreams are
generated. A public-channel message can itself contain private information;
the channel boundary does not classify message content. Rejected candidates
and historical summaries remain stored until separate retention work removes
their source data.
