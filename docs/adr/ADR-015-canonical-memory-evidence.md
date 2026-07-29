# ADR-015: Canonical memory with inspectable evidence

## Decision

Treat a Bottle's memory as its current canonical belief about one user, separate
from the sediment candidates and messages that support that belief.

Each active `user_memories` row may have many `user_memory_evidence` links.
Approving an exact normalized repetition attaches new evidence to the existing
memory. Similar but non-identical memories remain separate until an operator
accepts a persistent consolidation proposal. Acceptance moves all evidence to
one canonical row and marks the redundant rows as merged redirects. It never
deletes provenance.

Retrieval remains exact-first: scoped FTS matches are selected before stable
relationship/identity context and recent fallback memories. The configured LLM
may be invoked explicitly to propose semantically redundant groups, but it
cannot attach evidence, edit a memory, or merge rows.

## Alternatives considered

### Keep one approved memory per candidate

This preserves provenance simply, but repeated phrasing overwhelms the prompt
budget and hides genuinely distinct beliefs.

### Automatically merge semantic similarity

This reduces operator work but can collapse contradictions, changed facts, or
roleplay that is valid for one character perspective. Similar topic is not
equivalent belief.

### Replace old rows during consolidation

This produces a tidy table but destroys the inspectable path from current
belief to prior wording and source messages.

### Require embeddings

Embeddings can help find candidates, but making them canonical would add a
service dependency and make merge behavior harder to explain. The system keeps
SQLite FTS and structured scope checks as its reliable base.

## Reason chosen

The character engine needs both sanity and accountability. Canonical beliefs
bound prompt growth; many evidence links preserve why the Bottle believes each
fact. Bottle/user scope remains enforced in SQLite, and every semantic judgment
requires a visible operator decision.

## Tradeoffs

The operator must review semantic proposals, and the LLM scan costs an explicit
extra call per batch. Archived redirects and evidence tables add schema and UI
complexity. In return, no model silently rewrites character memory, rejected
groups are remembered, and repeated references strengthen one inspectable
belief instead of creating hundreds of prompt competitors.
