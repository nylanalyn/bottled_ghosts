# ADR-001: IRC identity and exact retrieval

## Decision

Resolve each incoming IRC speaker to a UUID before logging the message. Store
observed nick, account, and hostmask combinations in SQLite with an explicit
confidence value. Prefer account, then hostmask, then nick when resolving.

Index message bodies with an external-content SQLite FTS5 table maintained by
triggers. Search FTS before prompt construction and include scoped results as a
separate prompt layer.

## Alternatives considered

- Nick-only identity was rejected because nicks change and can be reused.
- Account-only identity was rejected because many networks and users do not
  expose an account tag.
- Embedding retrieval was rejected as a core dependency because exact local
  search is inspectable and works offline.
- Letting the model request searches was rejected because retrieval must happen
  deterministically before generation.

## Reason chosen

The ordered identity evidence works with ordinary IRC while improving when the
server supports `account-tag`. UUIDs keep message ownership stable across nick
changes. FTS5 is local, auditable, and already part of SQLite.

## Tradeoffs

Nick-only matches have low confidence and can be wrong after nick reuse.
Hostmasks may change or be cloaked. Account tags require IRCv3 capability
support. FTS finds lexical matches rather than semantic similarity. Explicit
UUID merging remains available when operators discover a mistaken split.
