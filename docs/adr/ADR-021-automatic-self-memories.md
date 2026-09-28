# ADR-021: Automatic self-memories from recollection chunks

## Decision

While the recollection job summarizes a closed public-channel chunk that
contains the Bottle's own lines, the same model call also returns up to three
`self_notes`: durable things the Bottle said about itself (tastes, opinions,
its own projects, how it feels about a specific person, running jokes,
promises). They are stored in `self_memories` without operator review,
deduplicated on normalized text, and retrieved per reply by exact FTS match,
labeled as the Bottle's own earlier words. Self-notes are independent of the
chunk's keep/discard decision. Private (`@`) conversations never produce them.
Operators can list and archive them with `self-memories` and
`self-memory-archive`.

## Alternatives considered

- Routing self statements through the sediment review queue would add the
  operator workload that recollection mode was introduced to remove.
- A separate extraction call per chunk or per reply doubles LLM cost.
- Folding them into the soul file would make the soul drift without review
  and grow without bound.
- Embedding search would add a dependency before exact search is evaluated.

## Reason chosen

The recollection call already reads the whole closed conversation, so asking
for self statements costs a few output tokens. Self-memories only describe the
character's own voice, never users, so the privacy and trust concerns that
justify reviewing user memories do not apply in the same way. Stored text,
counts, dates, and the source recollection remain inspectable.

## Tradeoffs

The model may record something the Bottle said in jest, or something a user
coaxed it into saying; archiving removes it from retrieval, and an archived
note stays archived if repeated. Contradictory statements coexist; retrieval
shows their dates so the newest reads as the current view. Only Bottles in
recollection mode build or use self-memories, and chunks processed before this
change are not revisited. Exact search misses paraphrases.
