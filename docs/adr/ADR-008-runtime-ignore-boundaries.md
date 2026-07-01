# ADR-008: Runtime ignore boundaries

## Decision

Per-Bottle IRC identity rules support two runtime-enforced actions. `drop` is
evaluated before identity resolution, logging, and module hooks. `no_response`
is evaluated before resolution but allows logging and hooks with
`response_allowed=False`; the runtime then prevents the message from opening or
extending a listening window.

Rules match exact account, hostmask, or RFC1459-casefolded nick values. If several
rules match, `drop` takes precedence.

## Alternatives considered

- Put ignore lists in Soul prompts. This cannot reliably prevent logging, module
  processing, or replies.
- Implement ignores as a module. A disabled or failed module would remove the
  protection and modules cannot run before hard-drop logging decisions.
- Use one global list. Different characters may intentionally have different
  interaction policies.

## Reason chosen

The runtime is the only layer able to guarantee invisibility and response denial
while retaining useful channel context for soft-ignored identities.

## Tradeoffs

Exact rules are predictable but require separate entries when an unauthenticated
user changes nick or hostmask. Account rules are preferred where IRC services
provide stable account tags.
