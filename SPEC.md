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
7. Scheduler
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

### bot_modules

Module enable state.

Fields:

* bot_id
* module_name
* enabled
* settings_json

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

### summaries

Nightly dreams.

Fields:

* id
* bot_id
* period_start
* period_end
* summary
* created_at

---

# Message Flow

Incoming message:

1. IRC receives message
2. Store raw message in SQLite
3. Resolve user UUID
4. Run enabled modules
5. Retrieve relevant memory
6. Build prompt
7. Call LLM
8. Sanitize output
9. Send reply
10. Extract candidate memories

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

Temporary state only.

---

# Nightly Dreaming

Each bottle may run nightly processing.

Tasks:

* summarize conversations
* evaluate sediment
* prune stale memory
* update bot state

Dreams are written in bot voice.

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
