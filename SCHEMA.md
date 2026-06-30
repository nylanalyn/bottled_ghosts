# Database schema

The schema below reflects migration 003.

## schema_migrations

Records applied schema versions. Columns: `version INTEGER PRIMARY KEY`, `applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP`.

## irc_profiles

Stores IRC connection configuration. Columns: `id INTEGER PRIMARY KEY`, `network TEXT NOT NULL`, `host TEXT NOT NULL`, `port INTEGER NOT NULL`, `tls INTEGER NOT NULL`, `nick TEXT NOT NULL`, `username TEXT NOT NULL`, `realname TEXT NOT NULL`, `channels TEXT NOT NULL`, `password TEXT`, `sasl_username TEXT`, `sasl_password TEXT`.

`channels` is a JSON-encoded list inside SQLite; SQLite remains canonical.

## llm_profiles

Stores OpenAI-compatible HTTP configuration. Columns: `id INTEGER PRIMARY KEY`, `endpoint TEXT NOT NULL`, `model TEXT NOT NULL`, `api_key TEXT`, `temperature REAL NOT NULL`, `max_tokens INTEGER NOT NULL`.

## bots

Stores bottle definitions and enforced output limits. Columns: `id INTEGER PRIMARY KEY`, `name TEXT NOT NULL UNIQUE`, `enabled INTEGER NOT NULL`, `soul_prompt_path TEXT NOT NULL`, `llm_profile_id INTEGER NOT NULL`, `irc_profile_id INTEGER NOT NULL`, `max_lines INTEGER NOT NULL`, `max_chars INTEGER NOT NULL`, `cooldown_seconds REAL NOT NULL`.

Foreign keys: `llm_profile_id` references `llm_profiles(id)`; `irc_profile_id` references `irc_profiles(id)`.

## messages

Stores incoming and outgoing IRC messages. Columns: `id INTEGER PRIMARY KEY`, `network TEXT NOT NULL`, `channel TEXT NOT NULL`, `speaker TEXT NOT NULL`, `timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP`, `body TEXT NOT NULL`, `bot_id INTEGER NOT NULL`, `user_id TEXT`.

Foreign keys: `bot_id` references `bots(id)`; `user_id` references `users(id)`. Indexes: `messages_context_idx(bot_id, network, channel, id DESC)` supports recent-context retrieval; `messages_user_idx(user_id, id DESC)` supports identity history.

## users

Stores canonical IRC users. Columns: `id TEXT PRIMARY KEY` containing a UUID, `canonical_name TEXT NOT NULL`, `created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP`.

## user_identities

Stores observed IRC identity evidence. Columns: `id INTEGER PRIMARY KEY`, `user_id TEXT NOT NULL`, `network TEXT NOT NULL`, `nick TEXT NOT NULL`, `account TEXT`, `hostmask TEXT`, `confidence REAL NOT NULL`, `first_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP`, `last_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP`.

Foreign key: `user_id` references `users(id)` with cascading deletion. Indexes: partial `user_identities_account_idx(network, account)`, partial `user_identities_hostmask_idx(network, hostmask)`, and `user_identities_nick_idx(network, nick COLLATE NOCASE)`.

## messages_fts

FTS5 external-content virtual table indexing `messages.body` with `messages.id` as its row ID. The `messages_fts_insert`, `messages_fts_delete`, and `messages_fts_update` triggers keep it synchronized with `messages`.

## Migration history

- 001: Add IRC profiles, LLM profiles, bottles, raw message logging, and recent-context index.
- 002: Add optional IRC SASL username and password fields.
- 003: Add UUID users, observed IRC identities, message ownership, and FTS5 message search.
