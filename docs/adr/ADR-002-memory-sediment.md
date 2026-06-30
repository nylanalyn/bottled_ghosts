# ADR-002: Memory sediment before trust

## Decision

Memory extraction is an explicit per-Bottle option. After a Bottle handles an
addressed message, a separate constrained LLM request may propose up to three
categorized candidates. Valid candidates are stored as pending sediment tied
to the source message and resolved user UUID. They are never used as approved
memory until an operator reviews them.

## Alternatives considered

- Automatically approving high-confidence output was rejected because model
  confidence is not evidence.
- Extracting from every channel message was rejected because it adds cost and
  records information from conversations the Bottle did not handle.
- Storing candidates in files was rejected because SQLite is canonical.
- Hiding extraction in a background worker was rejected because mutation and
  cost should remain visible in runtime logs.

## Reason chosen

Pending sediment makes uncertain model output inspectable and reversible. A
per-Bottle switch makes the extra LLM call deliberate. Source-message and UUID
foreign keys preserve provenance.

## Tradeoffs

Enabled extraction adds latency after the IRC reply and doubles LLM requests
for handled messages. Disabled Bottles collect no sediment. Strict JSON parsing
may reject otherwise useful malformed output; failures are logged and do not
interrupt IRC operation.
