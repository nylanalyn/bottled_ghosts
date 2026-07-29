# ADR-014: Bottle-scoped memory perspectives

## Decision

Sediment candidates and approved memories belong to exactly one Bottle.
Runtime retrieval requires both the Bottle ID and the resolved user UUID.
Message logs and user identity resolution remain shared because they describe
observable events and people, while memory records describe one character's
interpretation and relationship to them.

Migration 030 backfills candidate ownership from each source message and
approved-memory ownership from each source candidate. It refuses to migrate an
approved memory without usable provenance rather than assigning an invented
owner.

The operator interfaces display the owning Bottle for pending sediment and
approved memories.

## Alternatives considered

- Keeping one global memory pool and displaying only the extracting Bottle was
  rejected because the label would not prevent roleplay beliefs from entering
  unrelated characters' prompts.
- Giving every Bottle a separate SQLite database was rejected because shared
  logs, identities, configuration, and operator inspection remain useful.
- Copying every historical memory to every Bottle was rejected because it
  would preserve the perspective leak and manufacture ownership.
- Letting the prompt decide whether a shared memory applies was rejected
  because information boundaries must be enforced before prompt construction.

## Reason chosen

Each Bottle is a distinct character with its own experiences and relationships.
Database ownership makes that boundary visible, queryable, and enforceable.
Shared logs preserve a common factual event history without turning every
character's interpretation into universal truth.

## Tradeoffs

The same fact may be extracted and reviewed separately for several Bottles.
Operators must consider the Bottle owner during review. Historical approved
memories require intact source provenance for automatic migration; ambiguous
records stop migration for explicit operator handling.
