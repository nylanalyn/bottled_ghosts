# ADR-011: IRC and module runtime hardening

## Decision

IRC negotiation is driven by parsed commands and explicit registration state.
Registered connections enforce an idle timeout, send a client keepalive PING,
and disconnect if its PONG deadline expires. Ordered alternate nicks are stored
in SQLite and tried after `433`; authentication/configuration failures stop the
Bottle instead of entering the transport reconnect loop.

Modules are instantiated once per Bottle run and share startup, message,
nightly, and shutdown lifecycle. The first hook exception disables that module
for the remainder of the run, records degraded state for admin status, and does
not stop IRC or other modules.

Emergency markers are removed before public output and alerts are limited by a
persistent per-Bottle, per-location cooldown. At-most-once Discord event
delivery remains the explicit compatibility policy from ADR-010.

## Alternatives considered

- Raw substring CAP matching was rejected because message bodies can contain
  protocol words.
- Automatically generating fallback nick suffixes was rejected in favor of
  inspectable operator-configured choices.
- Persistently disabling a module after one runtime exception was rejected
  because transient failures should recover on explicit Bottle restart.
- Crashing the Bottle on a module failure was rejected because module isolation
  is a core runtime guarantee.

## Reason chosen

The runtime now distinguishes protocol, authentication, transport, and module
failures while preserving explicit operator control. Configured fallback nicks
and visible degraded state avoid hidden recovery behavior.

## Tradeoffs

A disabled module requires a Bottle restart to retry. Exhausted nick choices
still reconnect with transport backoff because collisions can clear. LLM-based
emergency classification remains prompt-injectable, but cooldowns limit paging
impact and alerts retain source provenance.
