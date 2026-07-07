# ADR-011: Per-Bottle local time context

## Decision

Store an IANA time-zone identifier on each Bottle in SQLite and calculate its local date and
time immediately before every generated response. Inject that value into the system context
as background information with an instruction to mention it only when relevant.

Existing Bottles default to `UTC`. Configuration accepts identifiers such as
`America/New_York` and rejects identifiers unavailable to Python's time-zone database.

## Alternatives considered

- Store a free-form city and use an online geocoder. This adds network dependence,
  ambiguity, and a failure mode unrelated to IRC operation.
- Store a fixed UTC offset. This becomes wrong when daylight-saving rules change.
- Calculate the time at startup. Long-running Bottles would receive stale context.
- Implement this as a behavior module. Local time is baseline prompt context and should not
  depend on optional module enablement.

## Reason chosen

IANA identifiers are precise, local-first, and daylight-saving-aware. Calculating on prompt
construction keeps the value fresh without hidden background jobs or additional persistent
state.

## Tradeoffs

Operators must provide a time-zone identifier rather than an arbitrary place name. Accuracy
depends on the host's installed time-zone database. The prompt gains a small fixed amount of
context on every generated response.
