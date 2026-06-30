# Database schema

The schema below reflects migration 001.

## schema_migrations

Records applied schema versions. Columns: `version INTEGER PRIMARY KEY`, `applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP`.

## irc_profiles

Stores IRC connection configuration. Columns: `id INTEGER PRIMARY KEY`, `network TEXT NOT NULL`, `host TEXT NOT NULL`, `port INTEGER NOT NULL`, `tls INTEGER NOT NULL`, `nick TEXT NOT NULL`, `username TEXT NOT NULL`, `realname TEXT NOT NULL`, `channels TEXT NOT NULL`, `password TEXT`.

`channels` is a JSON-encoded list inside SQLite; SQLite remains canonical.

## llm_profiles

Stores OpenAI-compatible HTTP configuration. Columns: `id INTEGER PRIMARY KEY`, `endpoint TEXT NOT NULL`, `model TEXT NOT NULL`, `api_key TEXT`, `temperature REAL NOT NULL`, `max_tokens INTEGER NOT NULL`.

## bots

Stores bottle definitions and enforced output limits. Columns: `id INTEGER PRIMARY KEY`, `name TEXT NOT NULL UNIQUE`, `enabled INTEGER NOT NULL`, `soul_prompt_path TEXT NOT NULL`, `llm_profile_id INTEGER NOT NULL`, `irc_profile_id INTEGER NOT NULL`, `max_lines INTEGER NOT NULL`, `max_chars INTEGER NOT NULL`, `cooldown_seconds REAL NOT NULL`.

Foreign keys: `llm_profile_id` references `llm_profiles(id)`; `irc_profile_id` references `irc_profiles(id)`.

## messages

Stores incoming and outgoing IRC messages. Columns: `id INTEGER PRIMARY KEY`, `network TEXT NOT NULL`, `channel TEXT NOT NULL`, `speaker TEXT NOT NULL`, `timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP`, `body TEXT NOT NULL`, `bot_id INTEGER NOT NULL`.

Foreign key: `bot_id` references `bots(id)`. Index: `messages_context_idx(bot_id, network, channel, id DESC)` supports recent-context retrieval. There are no FTS virtual tables in this migration.

## Migration history

- 001: Add IRC profiles, LLM profiles, bottles, raw message logging, and recent-context index.
