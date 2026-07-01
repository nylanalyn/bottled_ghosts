# ADR-004: Runtime-enforced module commands

## Decision

Modules may request an IRC game command by appending a `ModuleCommand` to their message context. The runtime sends at most one command per incoming message, requires the body to begin with `!`, applies the Bottle's character limit and cooldown, and records the outgoing command in SQLite.

## Alternatives considered

- Ask the LLM to emit game commands. This makes timing, ordering, and flood safety probabilistic.
- Give modules direct access to the IRC client. This bypasses common safety and message logging.
- Add a background scheduler. Fishing only needs to act when the configured channel is active, so a scheduler adds lifecycle behavior without improving the interaction.

## Reason chosen

The command request is explicit and inspectable while delivery remains under the same runtime safety controls as generated chat. Persisted module state determines whether casting or reeling is valid before a command is requested.

## Tradeoffs

Commands become due on the next channel message, not at an exact wall-clock instant. A lost game acknowledgement is retried after a bounded timeout; Jeeves' idempotent correction messages reconcile whether a cast exists.
