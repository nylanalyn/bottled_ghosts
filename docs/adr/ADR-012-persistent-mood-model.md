# ADR-012: Persistent two-axis mood model

## Decision

Implement mood as an optional module with global per-Bottle valence and
irritability axes, each bounded from -1 to 1. Each Bottle selects a data-driven
profile or overrides its baseline, volatility, sociability, reversion, quiet,
and interaction weights. Addressed messages always update mood; ambient channel
chatter is sampled at a configurable rate (default 5%) so a busy channel does
not pin interaction heat and drive irritability to its ceiling. Mood updates
are lazy and persisted in SQLite; prompt construction only reads and describes
the current state.

Interaction raises valence with diminishing returns. A decaying interaction
heat value raises irritability only above a configurable comfort threshold
(default 8.0). Elapsed time pulls mood toward its baseline, adds bounded random
drift, and can lower valence after a quiet grace period. The module starts no
scheduler.

## Alternatives considered

- A single happy-to-sad score cannot represent calm resignation separately
  from agitated unhappiness.
- Uniform random moods overproduce extremes and obscure character baselines.
- Bot-name branches in the module would couple character data to core logic.
- LLM room-sentiment classification adds cost and prompt-injection risk, while
  lexical sentiment routinely misclassifies IRC sarcasm and banter.

## Reason chosen

Two numeric axes produce varied combinations while remaining understandable
and directly inspectable. Baseline reversion keeps ordinary moods common, and
profiles express character differences without hardcoded bot behavior.
Message-driven updates satisfy the local-first, no-hidden-magic architecture.

## Tradeoffs

Interaction volume measures attention and overload, not whether conversation
is kind or hostile. Quiet-time effects are only materialized when another
message arrives. Random drift means identical histories can diverge, though
all resulting canonical state and the latest deltas are persisted.

## History

- Initial release counted every incoming IRC line as an interaction. In active
  channels this pinned heat at its cap within minutes and drove every Bottle to
  maximum irritability simultaneously. Corrected by gating ambient chatter
  behind a sample rate and raising the comfort threshold so only sustained,
  directly addressed conversation moves the needle.