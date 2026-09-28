# ADR-023: Per-task model overrides

## Decision

Each Bottle may override the model name for individual kinds of LLM call:
`reply`, `initiative`, `reflection`, `extraction`, `recollection`, `dream`,
`followup`, `summary`, and `consolidation`. Overrides live in
`bot_task_models` and load with the Bottle. Every call site asks
`Bottle.llm_for(task)`, which copies the main LLM profile with only the model
changed. `initiative` falls back to a `reply` override, and every task falls
back to the main model. Changes are audited in `configuration_events` and set
through the `task-model` CLI.

## Alternatives considered

- A full LLM profile per task (endpoint, key, sampling) would duplicate
  secrets and settings for a need that is only "same provider, different
  model", since an OpenAI-compatible router such as LiteLLM already maps model
  names to providers.
- Overriding temperature and token budgets per task would expose settings the
  code deliberately fixes for each job (JSON extraction at temperature 0,
  truncation retries with larger budgets).
- Configuring models through module settings JSON would not cover core jobs
  such as dreams, recollections, and replies.

## Reason chosen

Structured background jobs can run on cheaper models, and rare, high-leverage
jobs such as the nightly dream can use a larger model for pennies a day. The
reply voice stays on the model its soul was tuned against. One narrow table
keeps the change inspectable, and no row means today's behavior.

## Tradeoffs

A model name that the endpoint does not know fails only when that task runs:
a background job logs the failure, and a reply fails for that message. A
different model may follow the JSON contracts less reliably; the existing
parsing retries and fail-closed handling still apply. Running Bottles keep
the task models they loaded at startup until restarted.
