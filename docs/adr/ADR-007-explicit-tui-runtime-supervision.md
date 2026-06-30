# ADR-007: Explicit TUI runtime supervision

## Decision

The TUI may start and stop Bottle tasks only in response to an explicit operator
action. Each task uses the existing per-Bottle database connection boundary and
is cancelled and awaited when stopped or when the TUI exits.

The configured `enabled` flag remains distinct from live process state. Disabled
Bottles cannot be started, and changing configuration never implicitly starts,
stops, or restarts a task.

## Alternatives considered

- Start every enabled Bottle when the TUI opens. This creates hidden network
  activity from an inspection interface.
- Run child CLI processes. This adds process and signal-management complexity
  without improving isolation beyond the existing task and connection boundary.
- Persist live process state in SQLite. Process liveness is ephemeral and cannot
  be made accurate across crashes by storing a flag.

## Reason chosen

Explicit in-process supervision completes the operator workflow while preserving
the project's inspectability rules and existing runtime architecture.

## Tradeoffs

Closing the TUI stops Bottles that it started. Configuration changes still require
an explicit stop and start before a running Bottle observes them.
