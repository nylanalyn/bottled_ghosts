# ADR-004: Explicit dream jobs

## Decision

Nightly summarization is exposed as explicit `dream` and `dream-all` commands.
Operators may invoke them manually or schedule per-Bottle runs with cron or
systemd timers. Each run reads a bounded SQLite message window, calls the
configured LLM, stores the period and summary in SQLite, then invokes enabled
modules' `nightly` hooks. Recent summaries are retrieved before ordinary log
history.

The repository's Aria and Frauderick timers use `dream --sleep`. This temporarily
disables the Bottle's full public responses and module-generated IRC commands,
then restores the prior runtime-control state in a `finally` path. The IRC
connection remains online so incoming messages continue to be logged.

## Alternatives considered

- An implicit in-process scheduler was rejected because job timing and failures
  would be harder to inspect.
- Storing dream Markdown files was rejected because SQLite is canonical.
- Unbounded transcript summarization was rejected because channels can exceed
  model context and cost limits.

## Reason chosen

Explicit commands compose with mature operating-system schedulers and keep every
mutation visible. Stored period boundaries make it clear what a dream covered.
Bounded input prevents an active channel from exhausting the model context.

## Tradeoffs

Operators must configure or enable external scheduling if they want automatic
nightly runs. The first implementation summarizes at most 200 recent messages
in the requested window. Summaries span all channels observed by one Bottle.
