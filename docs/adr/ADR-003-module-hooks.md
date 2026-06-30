# ADR-003: Explicit module registry and isolated hooks

## Decision

Behavior modules expose one `Module` class with `on_message`, `before_prompt`,
`after_response`, and `nightly` async hooks. An explicit immutable registry maps
module names to constructors. Per-Bottle enable state is canonical in SQLite.
Every hook invocation is isolated so one module failure is logged without
stopping other modules or the IRC runtime.

## Alternatives considered

- Filesystem discovery and Python entry points were rejected as hidden magic.
- A dependency-injection framework was rejected as unnecessary machinery.
- Persisting module state in objects or files was rejected because persistent
  state belongs in SQLite.
- Allowing one hook exception to abort dispatch was rejected because modules
  must fail gracefully.

## Reason chosen

The registry makes installed behavior obvious in source. SQLite toggles make
operator intent inspectable. A small shared context supports prompt additions
and future database-backed behaviors without coupling modules to core branches.

## Tradeoffs

Adding a module requires a source change to the registry. Toggles are loaded
when a Bottle connects, so changing one requires reconnecting that Bottle.
Hook order follows module-name ordering and should not be treated as an implicit
dependency system.
