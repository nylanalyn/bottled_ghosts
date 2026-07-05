# Database schema

The schema below reflects migration 022.

## schema_migrations

Records applied schema versions. Columns: `version INTEGER PRIMARY KEY`, `applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP`.

## irc_profiles

Stores IRC connection configuration. Columns: `id INTEGER PRIMARY KEY`, `network TEXT NOT NULL`, `host TEXT NOT NULL`, `port INTEGER NOT NULL`, `tls INTEGER NOT NULL`, `nick TEXT NOT NULL`, `username TEXT NOT NULL`, `realname TEXT NOT NULL`, `channels TEXT NOT NULL`, `password TEXT`, `sasl_username TEXT`, `sasl_password TEXT`, `user_modes TEXT NOT NULL DEFAULT ''`, `alternate_nicks TEXT NOT NULL DEFAULT '[]'`.

`channels` and `alternate_nicks` are JSON-encoded lists inside SQLite; SQLite remains canonical.

## llm_profiles

Stores OpenAI-compatible HTTP configuration. Columns: `id INTEGER PRIMARY KEY`, `endpoint TEXT NOT NULL`, `model TEXT NOT NULL`, `api_key TEXT`, `temperature REAL NOT NULL`, `max_tokens INTEGER NOT NULL`, `frequency_penalty REAL NOT NULL DEFAULT 0.0` (CHECK between -2.0 and 2.0), `presence_penalty REAL NOT NULL DEFAULT 0.0` (CHECK between -2.0 and 2.0). The penalty fields are only sent to the completions endpoint when non-zero, so existing default-config Bottles keep their wire shape; they are forced to 0.0 for deterministic extraction and dream summarization.

## bots

Stores bottle definitions and enforced output limits. Columns: `id INTEGER PRIMARY KEY`, `name TEXT NOT NULL UNIQUE`, `enabled INTEGER NOT NULL`, `soul_prompt_path TEXT NOT NULL`, `llm_profile_id INTEGER NOT NULL`, `irc_profile_id INTEGER NOT NULL`, `max_lines INTEGER NOT NULL`, `max_chars INTEGER NOT NULL`, `cooldown_seconds REAL NOT NULL`, `listen_window_seconds REAL NOT NULL DEFAULT 8.0`, `extract_memories INTEGER NOT NULL DEFAULT 0`.

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

## irc_ignore_rules

Stores per-Bottle exact IRC identity rules. Columns: `id INTEGER PRIMARY KEY`, `bot_id INTEGER NOT NULL`, `network TEXT NOT NULL`, `match_type TEXT NOT NULL`, `match_value TEXT NOT NULL`, `action TEXT NOT NULL`, `created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP`. Match types are `account`, `hostmask`, and `nick`; actions are `drop` and `no_response`. Foreign key: `bot_id` references `bots(id)` with cascading deletion. Unique constraint: `(bot_id, network, match_type, match_value, action)`. Index: `irc_ignore_rules_lookup_idx(bot_id, network, match_type, match_value)`.

## memory_candidates

Stores unreviewed sediment proposed by the extractor. Columns: `id INTEGER PRIMARY KEY`, `user_id TEXT NOT NULL`, `source_message_id INTEGER NOT NULL`, `candidate_text TEXT NOT NULL`, `memory_type TEXT NOT NULL`, `confidence REAL NOT NULL`, `status TEXT NOT NULL DEFAULT 'pending'`, `created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP`, `reviewed_at TEXT`.

Foreign keys: `user_id` references `users(id)` with cascading deletion; `source_message_id` references `messages(id)` with cascading deletion. A unique constraint on `(user_id, source_message_id, candidate_text)` prevents duplicate extraction. Indexes: `memory_candidates_review_idx(status, created_at, id)` supports the review queue; `memory_candidates_user_idx(user_id, status, id DESC)` supports per-user inspection.

Allowed `memory_type` values are `preference`, `project`, `relationship`, `identity`, and `temporary_state`. Allowed statuses are `pending`, `approved`, and `rejected`.

## memory_candidate_sources

Stores the complete ordered message provenance used to extract a memory candidate. Columns: `candidate_id INTEGER NOT NULL`, `message_id INTEGER NOT NULL`, `ordinal INTEGER NOT NULL`. Primary key: `(candidate_id, message_id)`. Unique constraint: `(candidate_id, ordinal)`. Foreign keys reference `memory_candidates(id)` and `messages(id)` with cascading deletion. Index: `memory_candidate_sources_message_idx(message_id, candidate_id)`.

## user_memories

Stores operator-approved long-term memory. Columns: `id INTEGER PRIMARY KEY`, `user_id TEXT NOT NULL`, `source_candidate_id INTEGER UNIQUE`, `memory_text TEXT NOT NULL`, `memory_type TEXT NOT NULL`, `confidence REAL NOT NULL`, `created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP`, `updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP`, `last_used_at TEXT`, `expires_at TEXT`.

Foreign keys: `user_id` references `users(id)` with cascading deletion; `source_candidate_id` references `memory_candidates(id)` with deletion setting it to null. Indexes: `user_memories_user_idx(user_id, memory_type, id DESC)` and partial `user_memories_expiry_idx(expires_at)`. Memory types use the same five-value constraint as sediment. Expired rows remain available for inspection but are excluded from prompt retrieval.

## audit_events

Append-only operator mutation history. Columns: `id INTEGER PRIMARY KEY`, `action TEXT NOT NULL`, `entity_type TEXT NOT NULL`, `entity_id INTEGER NOT NULL`, `related_entity_id INTEGER`, `actor TEXT NOT NULL`, `old_text TEXT`, `new_text TEXT`, `old_type TEXT`, `new_type TEXT`, `old_confidence REAL`, `new_confidence REAL`, `old_status TEXT`, `new_status TEXT`, `old_expires_at TEXT`, `new_expires_at TEXT`, `created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP`.

Allowed actions are `approve`, `reject`, and `edit`. Allowed entity types are `memory_candidate` and `user_memory`. Index: `audit_events_entity_idx(entity_type, entity_id, id DESC)`. The `audit_events_no_update` and `audit_events_no_delete` triggers enforce append-only storage.

## bot_modules

Stores per-Bottle module enablement and future module settings. Columns: `bot_id INTEGER NOT NULL`, `module_name TEXT NOT NULL`, `enabled INTEGER NOT NULL DEFAULT 1`, `settings_json TEXT NOT NULL DEFAULT '{}'`. Primary key: `(bot_id, module_name)`. Foreign key: `bot_id` references `bots(id)` with cascading deletion. Index: `bot_modules_enabled_idx(bot_id, enabled, module_name)`.

## ambient_chat_state

Stores the ambient-chat module's persisted per-channel progress. Columns: `bot_id INTEGER NOT NULL`, `network TEXT NOT NULL`, `channel TEXT NOT NULL`, `eligible_lines_seen INTEGER NOT NULL DEFAULT 0`, `next_trigger_line INTEGER NOT NULL`, `updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP`. Primary key: `(bot_id, network, channel)`. Foreign key: `bot_id` references `bots(id)` with cascading deletion.

## fishing_state

Stores inspectable per-Bottle, per-channel fishing progress. Columns: `bot_id INTEGER NOT NULL`, `network TEXT NOT NULL`, `channel TEXT NOT NULL`, `phase TEXT NOT NULL`, `eligible_lines_seen INTEGER NOT NULL DEFAULT 0`, `next_cast_line INTEGER NOT NULL`, `cast_at INTEGER`, `reel_after INTEGER`, `command_sent_at INTEGER`, `banned_until INTEGER`, `updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP`. Unix timestamps are used for game deadlines. Primary key: `(bot_id, network, channel)`. Foreign key: `bot_id` references `bots(id)` with cascading deletion. Index: `fishing_state_due_idx(bot_id, network, phase, reel_after, banned_until)`. Allowed phases are `idle`, `awaiting_cast`, `fishing`, `awaiting_reel`, `awaiting_dynamite`, and `banned`.

## summaries

Stores Bottle dream summaries with explicit coverage periods. Columns: `id INTEGER PRIMARY KEY`, `bot_id INTEGER NOT NULL`, `period_start TEXT NOT NULL`, `period_end TEXT NOT NULL`, `summary TEXT NOT NULL`, `created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP`. Foreign key: `bot_id` references `bots(id)` with cascading deletion. Index: `summaries_bot_period_idx(bot_id, period_end DESC, id DESC)`.

## configuration_events

Append-only Bottle configuration audit history. Columns: `id INTEGER PRIMARY KEY`, `bot_id INTEGER NOT NULL`, `actor TEXT NOT NULL`, `changed_fields TEXT NOT NULL`, `old_value TEXT`, `new_value TEXT`, `created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP`. Non-secret values are JSON-encoded when a change has structured detail; secret values are never recorded. Foreign key: `bot_id` references `bots(id)` with cascading deletion. Index: `configuration_events_bot_idx(bot_id, id DESC)`. The `configuration_events_no_update` and `configuration_events_no_delete` triggers enforce append-only storage.

## bot_runtime_control

Stores persistent operational response control separately from Bottle enablement and process liveness. Columns: `bot_id INTEGER PRIMARY KEY`, `response_enabled INTEGER NOT NULL DEFAULT 1`, `updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP`. Foreign key: `bot_id` references `bots(id)` with cascading deletion.

## admin_events

Stores inspectable outbound administration events. Columns: `id INTEGER PRIMARY KEY`, `bot_id INTEGER NOT NULL`, `event_type TEXT NOT NULL`, `message TEXT NOT NULL`, `source_message_id INTEGER`, `created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP`, `delivered_at TEXT`. Foreign keys reference `bots(id)` with cascading deletion and `messages(id)` with `ON DELETE SET NULL`. Unique constraint: `(bot_id, event_type, source_message_id)`. Index: `admin_events_delivery_idx(bot_id, delivered_at, id)`.

## admin_api_credentials

Stores the per-Bottle admin API bearer token. Columns: `bot_id INTEGER PRIMARY KEY`, `token TEXT NOT NULL`, `updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP`. Foreign key: `bot_id` references `bots(id)` with cascading deletion. Token values are never copied into audit rows.

## bot_aliases

Stores additional names that address a Bottle. Columns: `bot_id INTEGER NOT NULL`, `alias TEXT NOT NULL`, `alias_key TEXT NOT NULL`, `created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP`. Primary key: `(bot_id, alias_key)`. Foreign key: `bot_id` references `bots(id)` with cascading deletion. Index: `bot_aliases_lookup_idx(bot_id, alias_key)`. `alias_key` uses IRC case folding so equivalent bracket and case variants cannot be duplicated.

## emergency_alert_state

Stores the last successful emergency alert time per Bottle and IRC location for runtime-enforced paging cooldowns. Columns: `bot_id INTEGER NOT NULL`, `network TEXT NOT NULL`, `channel TEXT NOT NULL`, `last_alert_at INTEGER NOT NULL`. Primary key: `(bot_id, network, channel)`. Foreign key: `bot_id` references `bots(id)` with cascading deletion.

## anti_repeat_state

Stores the optional anti-repeat module's per-channel flag. Columns: `bot_id INTEGER NOT NULL`, `network TEXT NOT NULL`, `channel TEXT NOT NULL`, `flag_for_next_prompt INTEGER NOT NULL DEFAULT 0` (CHECK 0 or 1), `updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP`. Primary key: `(bot_id, network, channel)`. Foreign key: `bot_id` references `bots(id)` with cascading deletion. The flag is set in `after_response` when a reply is too similar to a recent one (Sørensen–Dice over token bigrams, default ≥0.70) and consumed and cleared by the next `before_prompt`, which then injects a stronger "vary your angle" note. The bot's recent replies themselves are read on demand from `messages`, not stored here.

## maintenance_events

Append-only history of explicit maintenance jobs. Columns: `id INTEGER PRIMARY KEY`, `actor TEXT NOT NULL`, `action TEXT NOT NULL`, `details TEXT NOT NULL`, `created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP`. The `maintenance_events_no_update` and `maintenance_events_no_delete` triggers enforce append-only storage.

## Migration history

- 001: Add IRC profiles, LLM profiles, bottles, raw message logging, and recent-context index.
- 002: Add optional IRC SASL username and password fields.
- 003: Add UUID users, observed IRC identities, message ownership, and FTS5 message search.
- 004: Add per-Bottle extraction control and the pending memory-candidate review queue.
- 005: Add approved user memories and append-only review/edit audit events.
- 006: Add canonical per-Bottle module enablement and settings.
- 007: Add persistent Bottle dream summaries with explicit period boundaries.
- 008: Add append-only, secret-free Bottle configuration audit events.
- 009: Add the per-Bottle listening-window duration.
- 010: Add explicit temporary-memory expiry and expiry audit fields.
- 011: Add ordered multi-message provenance for memory candidates.
- 012: Add secret-free old and new values to configuration audit events.
- 013: Add per-profile IRC user modes and runtime-enforced identity ignore rules.
- 014: Add persisted per-channel state for the optional ambient-chat module.
- 015: Add persisted per-channel state and deadlines for the optional fishing module.
- 016: Add persistent response control, outbound administration events, and admin API credentials.
- 017: Add canonical per-Bottle address aliases.
- 018: Add ordered fallback nick configuration to IRC profiles.
- 019: Add persistent per-location emergency alert cooldown state.
- 020: Add append-only maintenance history for explicit retention jobs.
- 021: Add OpenAI-compatible frequency and presence penalty fields to LLM profiles, defaulting to 0.0 so existing Bottles keep their wire shape.
- 022: Add per-channel flag state for the optional anti-repeat module.
