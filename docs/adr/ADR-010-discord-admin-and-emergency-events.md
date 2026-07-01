# ADR-010: Discord administration and emergency events

## Decision

Optional `admin_api` and `emergency_alert` modules provide Discord-facing administration through the existing `ircbot_core` router contract. Module startup and shutdown hooks run for the lifetime of a Bottle task, outside IRC reconnect attempts. Silent-response state and outbound events are canonical in SQLite. Silent mode suppresses public replies while addressed emergency evaluation remains active.

The compatibility events endpoint marks returned events delivered. This avoids replaying alerts when the unchanged Discord router restarts, but delivery is at-most-once because the legacy protocol has no acknowledgement request.

## Alternatives considered

- Embedding a Discord client would duplicate the working shared router and its authorization controls.
- In-memory mute flags and event queues would be lost on restart and would hide operational state.
- Monitoring every IRC line would increase model use and false alerts; only direct messages and nick mentions are evaluated while silent.
- At-least-once delivery requires extending both sides with acknowledgements and was rejected for the compatibility release.

## Reason chosen

The existing router remains unchanged, operators retain familiar commands, and all durable behavior remains inspectable. Service lifecycle hooks keep status and controls reachable while IRC reconnects without coupling Discord code to the core runtime.

## Tradeoffs

Each Bottle needs a unique loopback port and token. A router failure after fetching but before sending can lose an alert. Emergency classification depends on the configured model following a strict marker contract.
