# Bottled Ghosts

An IRC-native multi-bot AI framework for persistent character-driven agents with memory, modular behaviors, and human-auditable state.

## Overview

Bottled Ghosts is a local-first system for running multiple AI-powered IRC bots (“ghosts”), each with unique personalities, memory, and optional behavioral modules.

Each bot is called a **Bottle**.

Each Bottle has:

* IRC identity and connection settings
* LLM configuration
* Personality prompt (“Soul”)
* Optional behavior modules
* Persistent memory
* Searchable historical logs

The system is managed through a terminal user interface (TUI) and stores all state in SQLite.

## Goals

* Run multiple AI bots simultaneously
* Support IRC-first communication
* Enforce IRC-safe message output
* Provide reliable memory without fragile embedding dependence
* Make all state auditable and editable by humans
* Allow modular behaviors reusable across bots
* Keep architecture simple and local-first

## Non-Goals

* Fully autonomous agents with unrestricted tool execution
* Hidden memory mutation without audit
* Prompt-only memory systems
* Cloud dependency

---

# Core Concepts

## Bottle

A running AI bot instance.

Contains:

* identity
* IRC connection
* soul prompt
* model configuration
* enabled modules

## Soul

A markdown file containing personality, behavior, lore, and speaking style.

Examples:

* librarian
* human impersonator
* absent-owner stand-in
* strange AI entity

## Module

Reusable behavioral plugin.

Examples:

* user_memory
* dreams
* needs
* defrag
* emergency_ping
* away_mode

Modules can be enabled per bottle.

Per-Bottle module settings are stored as validated JSON objects in SQLite and
provided to every hook through `ctx.module_settings`. Configuration changes are
operator-audited and require reconnecting a running Bottle to apply.

## Sediment

Unreviewed memory candidates.

Memories extracted from conversations are not immediately trusted.

They enter a review queue.

## Dreams

Nightly summarization jobs.

Dreams summarize:

* channel activity
* notable events
* memory candidates
* emotional state

Dream output can be stored as memory.

---

# Architecture

## Runtime Components

1. IRC Gateway
2. Message Bus
3. Prompt Builder
4. LLM Client
5. Memory Engine
6. Module Manager
7. Explicit Dream Job Runner
8. TUI
9. SQLite Storage

---

# Technology Stack

## Language

Primary language: Python 3.12+

Reasons:

* mature async ecosystem
* excellent LLM tooling
* easier rapid iteration
* strong TUI libraries

## Libraries

Suggested:

* asyncio
* aiosqlite
* textual (TUI)
* pydantic
* httpx
* structlog
* tenacity

Optional:

* sqlite-vec
* sentence-transformers
* numpy

---

# Database

Single SQLite database.

File:

`spirits.db`

## Tables

### bots

Stores bottle definitions.

Fields:

* id
* name
* enabled
* soul_prompt_path
* llm_profile_id
* irc_profile_id
* max_lines
* max_chars
* cooldown_seconds
* listen_window_seconds
* extract_memories

### irc_profiles

Stores IRC connection and authentication configuration referenced by `bots`.

Fields:

* id
* network
* host
* port
* tls
* nick
* username
* realname
* channels
* password
* sasl_username
* sasl_password
* user_modes

### llm_profiles

Stores OpenAI-compatible model configuration referenced by `bots`.

Fields:

* id
* endpoint
* model
* api_key
* temperature
* max_tokens

### bot_modules

Module enable state.

Fields:

* bot_id
* module_name
* enabled
* settings_json

### irc_ignore_rules

Per-Bottle IRC identity rules enforced before response selection.

Fields:

* id
* bot_id
* network
* match_type (`account`, `hostmask`, or `nick`)
* match_value
* action (`drop` or `no_response`)
* created_at

### ambient_chat_state

Structured persistent state for the optional ambient-chat module.

Fields:

* bot_id
* network
* channel
* eligible_lines_seen
* next_trigger_line

### users

Canonical users.

Fields:

* id (UUID)
* canonical_name
* created_at

### user_identities

Maps nick/account changes.

Fields:

* user_id
* network
* nick
* account
* hostmask
* confidence
* first_seen
* last_seen

### messages

Raw IRC logs.

Fields:

* id
* network
* channel
* speaker
* user_id
* timestamp
* body
* bot_id

### messages_fts

FTS5 virtual table.

Used for fast search.

### user_memories

Approved long-term user memories.

Fields:

* id
* user_id
* memory_text
* memory_type
* confidence
* created_at
* last_used_at

### memory_candidates

Pending memories.

Fields:

* id
* user_id
* source_message_id
* candidate_text
* type
* confidence
* status

### memory_candidate_sources

Ordered links from each candidate to every message used during extraction.

### summaries

Nightly dreams.

Fields:

* id
* bot_id
* period_start
* period_end
* summary
* created_at

### audit_events

Append-only history of sediment approval, rejection, and memory edits.

### configuration_events

Append-only, secret-free history of Bottle configuration changes.

### schema_migrations

Records each applied migration version and timestamp.

---

# Message Flow

Incoming message:

1. IRC receives message
2. Match runtime ignore rules against the raw IRC identity
3. If a `drop` rule matches: stop without logging, resolving, or running hooks
4. Resolve user UUID
5. Store raw message in SQLite
6. Run `on_message` module hooks with response eligibility
7. If a `no_response` rule matches: keep the message as context but do not open a response window
8. If addressed or an enabled module explicitly requests a response: start or extend a window
9. When the window expires: retrieve relevant memory
10. Run `before_prompt` module hooks
11. Build prompt from accumulated window messages
12. Call LLM
13. Sanitize output
14. Run `after_response` module hooks
15. Send reply
16. Extract candidate memories

The runtime is the final authority on response eligibility. Prompts and modules
cannot override an ignore rule.

---

# IRC User Modes

Each IRC profile may define a normalized user-mode string such as `+B` or `+Bi`.
After registration succeeds (`001`) and before joining channels, the runtime sends:

`MODE <current-nick> <configured-modes>`

Modes are per Bottle and operator-configurable through CLI and TUI. Empty means
no explicit mode command. Mode configuration is audited like other non-secret
IRC settings. Invalid whitespace or command separators are rejected before
storage so the field cannot inject additional IRC protocol lines.

---

# Ignore Rules

Ignore rules are per Bottle because different characters may be allowed to
interact with different speakers. Rules use exact identity matching, with IRC
account names preferred for services-authenticated users. Nick and hostmask are
available when no stable account exists.

Two actions exist:

* `drop`: the Bottle does not log, resolve, retrieve, or run module hooks for the
  message. From that Bottle's perspective, the message did not occur.
* `no_response`: the message is logged and may appear in later channel context,
  but it cannot start or extend a listening window and cannot directly trigger
  an ambient response.

If multiple rules match, `drop` wins. Ignore matching occurs in runtime code,
never through prompt instructions. Rules and their mutations are stored in
SQLite and recorded in the configuration audit trail.

The expected use for other bots is `no_response`: their messages remain useful
channel context without causing bot-to-bot reply loops. `drop` is reserved for
content that should not enter this Bottle's logs or context at all.

---

# Listening Window

The bot does not reply to each addressed message the instant it arrives.

When an addressed message is received, a per-(channel, nick) timer starts.
Each additional message from the same nick in the same channel resets the timer.
When the timer expires with no new input, all accumulated messages are joined in
order and treated as a single turn for prompt construction and LLM generation.

This means a user can send a multi-line thought without getting a reply after
each line.

Rules:

* Non-addressed messages from other nicks are logged normally and do not start a window.
* Only one response is generated per window expiry, regardless of how many messages accumulated.
* `on_message` fires for every incoming message.
* `before_prompt` and `after_response` fire once per window, when a reply is generated.
* If no messages arrive for `listen_window_seconds`, the window fires.

`listen_window_seconds` is a per-Bottle configuration field.

Default: 8 seconds.

---

# Prompt Construction

Prompt is built from layers.

Order:

1. Global IRC rules
2. Soul prompt
3. Module state
4. Relevant memories
5. Recent conversation
6. Current message

## Global IRC Rules

Always enforced:

* reply in 1–2 lines
* respect character limits
* avoid flooding
* avoid markdown formatting
* internal thoughts only inside `<think>` tags

---

# Memory System

Memory uses layered retrieval.

## Layer 1

Recent chat context.

## Layer 2

User memory.

Examples:

* likes cheese
* runs Linux
* dislikes JSON

## Layer 3

Dream summaries.

## Layer 4

Log search

Primary search:

SQLite FTS5

Secondary:

Optional semantic retrieval

Embedding search must never be required for core functionality.

---

# Memory Extraction

After conversations, a background extractor evaluates messages.

Possible outputs:

* durable preference
* project fact
* temporary state
* relationship
* discard

Example:

Input:

“I love cheese.”

Output:

Store permanently.

Input:

“I’m tired.”

Output:

Temporary state only. Approved `temporary_state` memories expire after 24 hours.
Expired memories remain inspectable and auditable but are not retrieved into prompts.

---

# Nightly Dreaming

Each bottle may run nightly processing.

Tasks:

* summarize conversations
* evaluate sediment
* prune stale memory
* update bot state

Dreams are written in bot voice.

Dream jobs are explicit commands scheduled by the operator through cron, systemd,
or another external scheduler. The runtime does not start hidden background jobs.

---

# Modules

Modules expose hooks.

Required hooks:

```python
on_message(ctx)
before_prompt(ctx)
after_response(ctx)
nightly(ctx)
```

Modules must be stateless or persist only via database.

No module may store canonical state in local files.

## Ambient Chat Module

`ambient_chat` is an optional per-Bottle module. It allows a character to speak
occasionally without a direct mention while keeping response authority in the
runtime.

Default settings:

* minimum eligible lines: 20
* maximum eligible lines: 40
* channel messages only; private messages never trigger ambient chat

For each `(bot_id, network, channel)`, the module stores an eligible-line counter
and a randomly selected next threshold in `ambient_chat_state`. The threshold is
chosen inclusively between the configured minimum and maximum and persisted so a
restart does not reroll or forget progress.

Only visible, response-eligible messages count. Messages matched by either ignore
level, the Bottle's own messages, and messages from `no_response` identities do
not advance or trigger the counter. Any reply by the Bottle resets the counter
and chooses the next threshold, preventing an ambient response immediately after
an addressed conversation.

When the threshold is reached, the module requests a normal listening window for
the current eligible message. Prompt construction, sanitization, cooldowns, and
memory behavior remain identical to addressed replies. Modules may request a
response but cannot send directly or bypass runtime ignore/flood controls.

---

# Output Sanitization

All LLM output must be sanitized before IRC.

Pipeline:

1. strip `<think>`
2. remove unsupported formatting
3. split into lines
4. enforce line count
5. enforce character limit
6. rate limit send

Runtime must enforce safety even if prompt fails.

---

# TUI

## Main Screen

Shows:

* all bottles
* status
* enabled state
* activity
* alerts

Actions:

* add bottle
* edit bottle
* start/stop bottle
* inspect logs

Starting and stopping a Bottle is always an explicit operator action. Closing the
TUI stops any Bottle tasks launched by that TUI. Configuration changes never
implicitly restart a running Bottle.

All persistent configuration mutations append a secret-free audit event with the
operator identity, changed field, and old/new non-secret values. No-op updates do
not create audit events.

## Bottle Screen

Sections:

### Identity

* nick
* username
* realname

### IRC

* server
* port
* channels
* auth

### LLM

* provider
* endpoint
* model
* api key
* temperature
* max tokens

### Modules

* enable/disable
* configure

### Memory

* view
* search
* edit
* review sediment

---

# Milestones

## v0.1

Core logging + IRC bot

Deliverables:

* connect to IRC
* log messages
* send replies
* SQLite storage

## v0.2

Prompt builder + souls

Deliverables:

* multiple bots
* soul prompts
* model profiles

## v0.3

Memory

Deliverables:

* user UUIDs
* memory extraction
* FTS search

## v0.4

Modules

Deliverables:

* module registry
* toggles
* shared hooks

## v0.5

Dreaming

Deliverables:

* nightly summaries
* sediment review

## v1.0

Full TUI

## v1.1

IRC participation controls

Deliverables:

* configurable per-Bottle IRC user modes
* runtime-enforced `drop` and `no_response` identity rules
* CLI and TUI management for ignore rules
* optional `ambient_chat` module with persisted 20–40-line random thresholds
* response-request module contract with runtime eligibility enforcement
* regression coverage for bot-loop prevention, hard-ignore invisibility, mode
  negotiation, threshold persistence, and flood-safe ambient replies
