# ADR-022: Quiet-room initiative through an on_idle hook

## Decision

Add an optional `on_idle(ctx: IdleContext)` module hook. While a Bottle is
connected, and only when an enabled module implements the hook, the runtime
checks each joined channel once a minute. Replies must be enabled and quiet
mode off, and the channel must not be on a mood break. A module that wants the
Bottle to speak sets `initiative_note`. The runtime then builds a prompt with
that situation in place of a quoted current message, runs the normal
`before_prompt`, `before_generation`, and `after_response` hooks with
`response_reason="initiative"`, re-checks the response controls, and sends
through the same sanitizer, line and character limits, typing pace, and
cooldown as every reply.

The `initiative` module offers an opening only when:

- the room has been quiet for a randomized lull (default 45 to 180 minutes);
- a human, not any Bottle in this database, spoke within `human_recent_hours`;
- a human has spoken since the previous offer by any Bottle in that channel;
- fewer than `max_per_day` offers were made there in the last 24 hours.

The model may decline with `[pass]`. `initiative_state` holds cadence and
`initiative_events` records every offer and whether it `spoke` or `passed`.

## Alternatives considered

- A systemd job cannot speak: only the connected runtime owns the IRC socket.
  A job writing requests for the runtime to poll is still a runtime poll, with
  an extra table and more latency.
- Scheduling a timer per channel on each incoming line would need restart
  recovery, and would still not fire in a room that stays silent after a
  restart.
- Letting ambient chat count elapsed time instead of lines would mix two
  different cadences in one module and one state row.

## Reason chosen

A fixed one-minute poll is the simplest mechanism that survives restarts: all
decision state is in SQLite, so a restarted Bottle continues the same cadence.
Keeping the decision in a module keeps character policy out of the runtime,
while the runtime keeps sole ownership of IRC output and its hard limits. The
human-since-last-offer rule rules out monologues and Bottle-to-Bottle chains
without needing to know which other IRC users are bots, because every Bottle
in the shared database is known.

## Tradeoffs

This is a background loop, which Rule 2 discourages; it is bounded to enabled
modules, logs each offer, and stores every decision. Bots that are not
Bottles in this database count as human activity unless they are listed in
`other_bots` or in ambient chat's `utility_bot_nicks`, or are dropped by the
ignore module. A Bottle may open a room where
nobody is currently reading, which is normal IRC behavior but means some
openers go unanswered. A pass still spends one of the day's offers.
