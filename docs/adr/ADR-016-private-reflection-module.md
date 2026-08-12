# ADR-016: Optional private reflection before response generation

## Decision

Add an opt-in `reflection` module with a `before_generation` hook. After the
runtime assembles the complete prompt—including history, exact retrieval,
memories, dreams, and module context—the module makes a bounded second LLM
call for concise private working notes. Those notes are appended only to the
in-memory generation prompt for the public response.

Reflection notes are never logged, stored as memory, or sent to IRC. If the
reflection call fails, the module logs the failure and the normal one-pass
response continues.

## Alternatives considered

- Putting reflection instructions in the ordinary prompt does not create a
  separate planning pass and makes the behavior dependent on the model obeying
  a formatting request.
- Rebuilding retrieval inside the module would duplicate core prompt logic and
  risk inconsistent context.
- Persisting reflections would turn transient interpretation into canonical
  character state without operator review.

## Reason chosen

The module can see exactly the context the public response will see while
remaining independently enabled per Bottle. A short, bounded reflection pass
may improve continuity and social judgment for character Bottles, while the
opt-in setting keeps cost and latency away from utility Bottles.

## Tradeoffs

Enabled Bottles make an additional LLM call per response, increasing latency and
cost. A mistaken reflection can bias the final response, so the notes are
explicitly untrusted and the final model is told to verify them. The feature is
being evaluated on Aria first.
