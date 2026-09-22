# ADR-018: Explicit automatic recollections

## Decision

Store a Bottle's fallible recollections as processed, source-linked conversation
chunks in SQLite. An explicit `recollect` job processes conversations after ten
minutes of inactivity, with limits on chunk size and work per run. Empty results
are stored so reruns do not call the model again. FTS5 retrieval is limited to
the same Bottle, network, and conversation; private conversations stay private.

Enabling recollection mode turns off per-reply semantic candidate extraction for
that Bottle. A direct `remember that ...` or `remember this: ...` request can
still create one pending semantic candidate from the requesting speaker's own
message. It follows the existing review and evidence path. Existing approved
memories and their evidence remain in use.
Operators can inspect and archive recollections. Archives remain inspectable.

## Alternatives considered

- A runtime inactivity task would require persistent timers and restart recovery.
- Reusing dreams would delay recollection until the nightly job and lose
  conversation boundaries.
- Automatically promoting summaries to semantic memory would turn fallible
  accounts into trusted beliefs without independent evidence.
- Embeddings would add a dependency before exact search has been evaluated.

## Reason chosen

An explicit scheduled job matches existing dream and maintenance operations.
SQLite progress and source links make repeated runs and operator inspection
straightforward. Conversation-scoped retrieval prevents private or unrelated
room content from crossing into a prompt.

## Tradeoffs

Operators must schedule the job. The fixed ten-minute gap can split long
conversations or delay recall. Source links prevent log pruning from reclaiming
those messages; a later retention policy must address actual database growth.
Semantic claims still need operator approval when per-reply extraction is used.
