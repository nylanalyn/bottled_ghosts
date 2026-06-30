# Database schema

The schema below reflects migration 006.

## schema_migrations

Records applied schema versions. Columns: `version INTEGER PRIMARY KEY`, `applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP`.

## irc_profiles

Stores IRC connection configuration. Columns: `id INTEGER PRIMARY KEY`, `network TEXT NOT NULL`, `host TEXT NOT NULL`, `port INTEGER NOT NULL`, `tls INTEGER NOT NULL`, `nick TEXT NOT NULL`, `username TEXT NOT NULL`, `realname TEXT NOT NULL`, `channels TEXT NOT NULL`, `password TEXT`, `sasl_username TEXT`, `sasl_password TEXT`.

`channels` is a JSON-encoded list inside SQLite; SQLite remains canonical.

## llm_profiles

Stores OpenAI-compatible HTTP configuration. Columns: `id INTEGER PRIMARY KEY`, `endpoint TEXT NOT NULL`, `model TEXT NOT NULL`, `api_key TEXT`, `temperature REAL NOT NULL`, `max_tokens INTEGER NOT NULL`.

## bots

Stores bottle definitions and enforced output limits. Columns: `id INTEGER PRIMARY KEY`, `name TEXT NOT NULL UNIQUE`, `enabled INTEGER NOT NULL`, `soul_prompt_path TEXT NOT NULL`, `llm_profile_id INTEGER NOT NULL`, `irc_profile_id INTEGER NOT NULL`, `max_lines INTEGER NOT NULL`, `max_chars INTEGER NOT NULL`, `cooldown_seconds REAL NOT NULL`, `extract_memories INTEGER NOT NULL DEFAULT 0`.

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

## memory_candidates

Stores unreviewed sediment proposed by the extractor. Columns: `id INTEGER PRIMARY KEY`, `user_id TEXT NOT NULL`, `source_message_id INTEGER NOT NULL`, `candidate_text TEXT NOT NULL`, `memory_type TEXT NOT NULL`, `confidence REAL NOT NULL`, `status TEXT NOT NULL DEFAULT 'pending'`, `created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP`, `reviewed_at TEXT`.

Foreign keys: `user_id` references `users(id)` with cascading deletion; `source_message_id` references `messages(id)` with cascading deletion. A unique constraint on `(user_id, source_message_id, candidate_text)` prevents duplicate extraction. Indexes: `memory_candidates_review_idx(status, created_at, id)` supports the review queue; `memory_candidates_user_idx(user_id, status, id DESC)` supports per-user inspection.

Allowed `memory_type` values are `preference`, `project`, `relationship`, `identity`, and `temporary_state`. Allowed statuses are `pending`, `approved`, and `rejected`.

## user_memories

Stores operator-approved long-term memory. Columns: `id INTEGER PRIMARY KEY`, `user_id TEXT NOT NULL`, `source_candidate_id INTEGER UNIQUE`, `memory_text TEXT NOT NULL`, `memory_type TEXT NOT NULL`, `confidence REAL NOT NULL`, `created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP`, `updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP`, `last_used_at TEXT`.

Foreign keys: `user_id` references `users(id)` with cascading deletion; `source_candidate_id` references `memory_candidates(id)` with deletion setting it to null. Index: `user_memories_user_idx(user_id, memory_type, id DESC)`. Memory types use the same five-value constraint as sediment.

## audit_events

Append-only operator mutation history. Columns: `id INTEGER PRIMARY KEY`, `action TEXT NOT NULL`, `entity_type TEXT NOT NULL`, `entity_id INTEGER NOT NULL`, `related_entity_id INTEGER`, `actor TEXT NOT NULL`, `old_text TEXT`, `new_text TEXT`, `old_type TEXT`, `new_type TEXT`, `old_confidence REAL`, `new_confidence REAL`, `old_status TEXT`, `new_status TEXT`, `created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP`.

Allowed actions are `approve`, `reject`, and `edit`. Allowed entity types are `memory_candidate` and `user_memory`. Index: `audit_events_entity_idx(entity_type, entity_id, id DESC)`. The `audit_events_no_update` and `audit_events_no_delete` triggers enforce append-only storage.

## bot_modules

Stores per-Bottle module enablement and future module settings. Columns: `bot_id INTEGER NOT NULL`, `module_name TEXT NOT NULL`, `enabled INTEGER NOT NULL DEFAULT 1`, `settings_json TEXT NOT NULL DEFAULT '{}'`. Primary key: `(bot_id, module_name)`. Foreign key: `bot_id` references `bots(id)` with cascading deletion. Index: `bot_modules_enabled_idx(bot_id, enabled, module_name)`.

## Migration history

- 001: Add IRC profiles, LLM profiles, bottles, raw message logging, and recent-context index.
- 002: Add optional IRC SASL username and password fields.
- 003: Add UUID users, observed IRC identities, message ownership, and FTS5 message search.
- 004: Add per-Bottle extraction control and the pending memory-candidate review queue.
- 005: Add approved user memories and append-only review/edit audit events.
- 006: Add canonical per-Bottle module enablement and settings.
