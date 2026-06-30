# AGENTS.md

@RTK.md

This document defines implementation constraints for coding agents working on Bottled Ghosts.

## Mission

Build a maintainable, local-first multi-bot IRC AI framework.

Prioritize clarity over cleverness.

---

# Core Rules

## Rule 1: SQLite is canonical

All persistent runtime state belongs in SQLite.

Never store canonical state in:

* JSON
* YAML
* markdown
* in-memory globals

Those formats may be used for import/export only.

## Rule 2: No hidden magic

State mutations must be visible and inspectable.

Avoid implicit background behavior.

Human operators must be able to understand why a bot behaved a certain way.

## Rule 3: Modular architecture

Bot-specific logic must not be hardcoded into core runtime.

Shared behavior belongs in modules.

## Rule 4: IRC safety is runtime enforced

Never trust prompts to prevent flooding.

Code must hard-enforce:

* line limits
* character limits
* cooldowns

## Rule 5: Retrieval before generation

Do not expect LLMs to decide when to search memory.

Memory retrieval must occur before prompt construction.

## Rule 6: Prefer exact search

Search priority:

1. SQLite FTS
2. structured lookup
3. semantic search

Embeddings are optional enhancement.

Never make embeddings a hard dependency.

---

# Code Standards

* Type hints required
* Pydantic models preferred
* Async-first networking
* Avoid global mutable state
* Small modules
* Single responsibility

---

# Project Structure

Expected layout:

```text
cellar/
bottles/
modules/
tui/
tests/
```

---

# Module Contract

Each module must expose:

```python
class Module:
    async def on_message(self, ctx): ...
    async def before_prompt(self, ctx): ...
    async def after_response(self, ctx): ...
    async def nightly(self, ctx): ...
```

Modules should fail gracefully.

One module failure must not crash runtime.

---

# Database Migrations

Never mutate schema ad hoc.

All schema changes require migrations.

Use explicit migration versions.

---

# Memory Policy

Memories must be categorized.

Allowed categories:

* preference
* project
* relationship
* identity
* temporary_state

Do not store sensitive inferred traits automatically.

---

# Testing Requirements

Required test coverage for:

* IRC flood protection
* prompt building
* memory extraction
* UUID identity merging
* module loading
* sanitization

---

# Forbidden Patterns

Do not introduce:

* giant god objects
* circular imports
* hidden singleton state
* bot-specific branches in core runtime
* prompt-only safety mechanisms

---

# Philosophy

Bottled Ghosts is not a general-purpose agent framework.

It is a character engine.

Bots should feel alive while remaining understandable, inspectable, and controllable.

## Schema Documentation Requirement

After each database migration, update `SCHEMA.md`.

`SCHEMA.md` must include:

* every table
* every column
* column types
* indexes
* foreign keys
* FTS virtual tables
* migration history
* purpose of each table

Schema documentation must reflect actual migration state, not planned architecture.

Never manually edit `SCHEMA.md` without corresponding migration changes.

## Architecture Decision Records

For major architectural changes, create a file in `/docs/adr/`.

Format:

ADR-001-memory-engine.md
ADR-002-module-hooks.md
ADR-003-embedding-strategy.md

Each ADR must explain:

* decision
* alternatives considered
* reason chosen
* tradeoffs
