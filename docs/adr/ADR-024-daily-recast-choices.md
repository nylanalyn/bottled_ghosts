# ADR-024: Daily recast with an in-character choice

## Decision

The fishing module sends one `!recast` a day instead of separate `!cast` and
`!reel` commands. Jeeves' `!recast` reels in the line that is out and casts
straight back, or just casts when no line is out, so a single command per day
keeps a Bottle fishing without tracking cast/reel phases. The next recast is
due 20–23.5 hours later, since Jeeves rewards a line left about a day and
starts losing catches past 24 hours.

About two hours before a recast is due, the module starts a background LLM
call with the Bottle's soul and Jeeves' last reply. The model picks a plain
recast, `lure`, `chum`, or both, and whether to follow with `!dynamite`. The
pick is stored in `fishing_schedule` and sent when due. This refines
ADR-004 rather than reversing it: the model chooses only among options the
module offers, and the runtime still decides when commands go out, sends at
most one per incoming message, and enforces the dynamite cooldown.

## Alternatives considered

- Keep random `!cast`/`!reel` timing. Two commands with acknowledgement
  phases added state and failure modes for no gameplay benefit.
- Let the reply model emit commands. Rejected for the same reasons as in
  ADR-004: timing and flood safety would be probabilistic.
- Choose inside `on_message`. That hook runs under the database lock, so a
  slow LLM call would stall the Bottle; the choice runs as a background task
  and takes the lock only to read and write its row.

## Tradeoffs

A choice costs one small LLM call per Bottle per day. If no channel message
arrives during the planning lead, the due recast waits up to 15 minutes for
the choice before going plain. The module reads Jeeves' replies only to keep
the latest outcome and to notice a ban or a line too fresh to reel; it does
not track XP, so a lure or chum the Bottle cannot afford is simply refused by
Jeeves and the recast goes ahead.
