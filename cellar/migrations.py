from collections.abc import Awaitable, Callable

import aiosqlite

Migration = Callable[[aiosqlite.Connection], Awaitable[None]]


async def migration_001(db: aiosqlite.Connection) -> None:
    await db.executescript(
        """
        CREATE TABLE irc_profiles (
            id INTEGER PRIMARY KEY,
            network TEXT NOT NULL,
            host TEXT NOT NULL,
            port INTEGER NOT NULL DEFAULT 6697,
            tls INTEGER NOT NULL DEFAULT 1 CHECK (tls IN (0, 1)),
            nick TEXT NOT NULL,
            username TEXT NOT NULL,
            realname TEXT NOT NULL,
            channels TEXT NOT NULL,
            password TEXT
        );
        CREATE TABLE llm_profiles (
            id INTEGER PRIMARY KEY,
            endpoint TEXT NOT NULL,
            model TEXT NOT NULL,
            api_key TEXT,
            temperature REAL NOT NULL DEFAULT 0.7,
            max_tokens INTEGER NOT NULL DEFAULT 160
        );
        CREATE TABLE bots (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
            soul_prompt_path TEXT NOT NULL,
            llm_profile_id INTEGER NOT NULL REFERENCES llm_profiles(id),
            irc_profile_id INTEGER NOT NULL REFERENCES irc_profiles(id),
            max_lines INTEGER NOT NULL DEFAULT 2 CHECK (max_lines > 0),
            max_chars INTEGER NOT NULL DEFAULT 400 CHECK (max_chars BETWEEN 1 AND 450),
            cooldown_seconds REAL NOT NULL DEFAULT 1.0 CHECK (cooldown_seconds >= 0)
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY,
            network TEXT NOT NULL,
            channel TEXT NOT NULL,
            speaker TEXT NOT NULL,
            timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            body TEXT NOT NULL,
            bot_id INTEGER NOT NULL REFERENCES bots(id)
        );
        CREATE INDEX messages_context_idx
            ON messages(bot_id, network, channel, id DESC);
        """
    )


async def migration_002(db: aiosqlite.Connection) -> None:
    await db.executescript(
        """
        ALTER TABLE irc_profiles ADD COLUMN sasl_username TEXT;
        ALTER TABLE irc_profiles ADD COLUMN sasl_password TEXT;
        """
    )


async def migration_003(db: aiosqlite.Connection) -> None:
    await db.executescript(
        """
        CREATE TABLE users (
            id TEXT PRIMARY KEY,
            canonical_name TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE user_identities (
            id INTEGER PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            network TEXT NOT NULL,
            nick TEXT NOT NULL,
            account TEXT,
            hostmask TEXT,
            confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
            first_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX user_identities_account_idx
            ON user_identities(network, account) WHERE account IS NOT NULL;
        CREATE INDEX user_identities_hostmask_idx
            ON user_identities(network, hostmask) WHERE hostmask IS NOT NULL;
        CREATE INDEX user_identities_nick_idx
            ON user_identities(network, nick COLLATE NOCASE);

        ALTER TABLE messages ADD COLUMN user_id TEXT REFERENCES users(id);
        CREATE INDEX messages_user_idx ON messages(user_id, id DESC);

        CREATE VIRTUAL TABLE messages_fts USING fts5(
            body,
            content='messages',
            content_rowid='id'
        );
        INSERT INTO messages_fts(rowid, body) SELECT id, body FROM messages;
        CREATE TRIGGER messages_fts_insert AFTER INSERT ON messages BEGIN
            INSERT INTO messages_fts(rowid, body) VALUES (new.id, new.body);
        END;
        CREATE TRIGGER messages_fts_delete AFTER DELETE ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, body)
            VALUES ('delete', old.id, old.body);
        END;
        CREATE TRIGGER messages_fts_update AFTER UPDATE OF body ON messages BEGIN
            INSERT INTO messages_fts(messages_fts, rowid, body)
            VALUES ('delete', old.id, old.body);
            INSERT INTO messages_fts(rowid, body) VALUES (new.id, new.body);
        END;
        """
    )


async def migration_004(db: aiosqlite.Connection) -> None:
    await db.executescript(
        """
        ALTER TABLE bots ADD COLUMN extract_memories INTEGER NOT NULL DEFAULT 0
            CHECK (extract_memories IN (0, 1));
        CREATE TABLE memory_candidates (
            id INTEGER PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            source_message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
            candidate_text TEXT NOT NULL,
            memory_type TEXT NOT NULL CHECK (
                memory_type IN ('preference', 'project', 'relationship', 'identity', 'temporary_state')
            ),
            confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
            status TEXT NOT NULL DEFAULT 'pending' CHECK (
                status IN ('pending', 'approved', 'rejected')
            ),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            reviewed_at TEXT,
            UNIQUE(user_id, source_message_id, candidate_text)
        );
        CREATE INDEX memory_candidates_review_idx
            ON memory_candidates(status, created_at, id);
        CREATE INDEX memory_candidates_user_idx
            ON memory_candidates(user_id, status, id DESC);
        """
    )


async def migration_005(db: aiosqlite.Connection) -> None:
    await db.executescript(
        """
        CREATE TABLE user_memories (
            id INTEGER PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            source_candidate_id INTEGER UNIQUE REFERENCES memory_candidates(id) ON DELETE SET NULL,
            memory_text TEXT NOT NULL,
            memory_type TEXT NOT NULL CHECK (
                memory_type IN ('preference', 'project', 'relationship', 'identity', 'temporary_state')
            ),
            confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_used_at TEXT
        );
        CREATE INDEX user_memories_user_idx
            ON user_memories(user_id, memory_type, id DESC);

        CREATE TABLE audit_events (
            id INTEGER PRIMARY KEY,
            action TEXT NOT NULL CHECK (action IN ('approve', 'reject', 'edit')),
            entity_type TEXT NOT NULL CHECK (entity_type IN ('memory_candidate', 'user_memory')),
            entity_id INTEGER NOT NULL,
            related_entity_id INTEGER,
            actor TEXT NOT NULL,
            old_text TEXT,
            new_text TEXT,
            old_type TEXT,
            new_type TEXT,
            old_confidence REAL,
            new_confidence REAL,
            old_status TEXT,
            new_status TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX audit_events_entity_idx
            ON audit_events(entity_type, entity_id, id DESC);
        CREATE TRIGGER audit_events_no_update BEFORE UPDATE ON audit_events BEGIN
            SELECT RAISE(ABORT, 'audit events are append-only');
        END;
        CREATE TRIGGER audit_events_no_delete BEFORE DELETE ON audit_events BEGIN
            SELECT RAISE(ABORT, 'audit events are append-only');
        END;
        """
    )


async def migration_006(db: aiosqlite.Connection) -> None:
    await db.executescript(
        """
        CREATE TABLE bot_modules (
            bot_id INTEGER NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
            module_name TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
            settings_json TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY (bot_id, module_name)
        );
        CREATE INDEX bot_modules_enabled_idx
            ON bot_modules(bot_id, enabled, module_name);
        """
    )


async def migration_007(db: aiosqlite.Connection) -> None:
    await db.executescript(
        """
        CREATE TABLE summaries (
            id INTEGER PRIMARY KEY,
            bot_id INTEGER NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
            period_start TEXT NOT NULL,
            period_end TEXT NOT NULL,
            summary TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CHECK (period_start < period_end)
        );
        CREATE INDEX summaries_bot_period_idx
            ON summaries(bot_id, period_end DESC, id DESC);
        """
    )


async def migration_008(db: aiosqlite.Connection) -> None:
    await db.executescript(
        """
        CREATE TABLE configuration_events (
            id INTEGER PRIMARY KEY,
            bot_id INTEGER NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
            actor TEXT NOT NULL,
            changed_fields TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX configuration_events_bot_idx
            ON configuration_events(bot_id, id DESC);
        CREATE TRIGGER configuration_events_no_update
        BEFORE UPDATE ON configuration_events BEGIN
            SELECT RAISE(ABORT, 'configuration events are append-only');
        END;
        CREATE TRIGGER configuration_events_no_delete
        BEFORE DELETE ON configuration_events BEGIN
            SELECT RAISE(ABORT, 'configuration events are append-only');
        END;
        """
    )


async def migration_009(db: aiosqlite.Connection) -> None:
    await db.execute(
        """ALTER TABLE bots ADD COLUMN listen_window_seconds REAL NOT NULL DEFAULT 8.0
           CHECK (listen_window_seconds > 0)"""
    )


async def migration_010(db: aiosqlite.Connection) -> None:
    await db.executescript(
        """
        ALTER TABLE user_memories ADD COLUMN expires_at TEXT;
        ALTER TABLE audit_events ADD COLUMN old_expires_at TEXT;
        ALTER TABLE audit_events ADD COLUMN new_expires_at TEXT;
        CREATE INDEX user_memories_expiry_idx
            ON user_memories(expires_at) WHERE expires_at IS NOT NULL;
        """
    )


async def migration_011(db: aiosqlite.Connection) -> None:
    await db.executescript(
        """
        CREATE TABLE memory_candidate_sources (
            candidate_id INTEGER NOT NULL
                REFERENCES memory_candidates(id) ON DELETE CASCADE,
            message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
            ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
            PRIMARY KEY (candidate_id, message_id),
            UNIQUE(candidate_id, ordinal)
        );
        CREATE INDEX memory_candidate_sources_message_idx
            ON memory_candidate_sources(message_id, candidate_id);
        INSERT INTO memory_candidate_sources(candidate_id, message_id, ordinal)
            SELECT id, source_message_id, 0 FROM memory_candidates;
        """
    )


async def migration_012(db: aiosqlite.Connection) -> None:
    await db.executescript(
        """
        ALTER TABLE configuration_events ADD COLUMN old_value TEXT;
        ALTER TABLE configuration_events ADD COLUMN new_value TEXT;
        """
    )


async def migration_013(db: aiosqlite.Connection) -> None:
    await db.executescript(
        """
        ALTER TABLE irc_profiles ADD COLUMN user_modes TEXT NOT NULL DEFAULT '';
        CREATE TABLE irc_ignore_rules (
            id INTEGER PRIMARY KEY,
            bot_id INTEGER NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
            network TEXT NOT NULL,
            match_type TEXT NOT NULL CHECK (match_type IN ('account', 'hostmask', 'nick')),
            match_value TEXT NOT NULL,
            action TEXT NOT NULL CHECK (action IN ('drop', 'no_response')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(bot_id, network, match_type, match_value, action)
        );
        CREATE INDEX irc_ignore_rules_lookup_idx
            ON irc_ignore_rules(bot_id, network, match_type, match_value);
        """
    )


async def migration_014(db: aiosqlite.Connection) -> None:
    await db.executescript(
        """
        CREATE TABLE ambient_chat_state (
            bot_id INTEGER NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
            network TEXT NOT NULL,
            channel TEXT NOT NULL,
            eligible_lines_seen INTEGER NOT NULL DEFAULT 0 CHECK (eligible_lines_seen >= 0),
            next_trigger_line INTEGER NOT NULL CHECK (next_trigger_line > 0),
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (bot_id, network, channel)
        );
        """
    )


async def migration_015(db: aiosqlite.Connection) -> None:
    await db.executescript(
        """
        CREATE TABLE fishing_state (
            bot_id INTEGER NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
            network TEXT NOT NULL,
            channel TEXT NOT NULL,
            phase TEXT NOT NULL CHECK (
                phase IN ('idle', 'awaiting_cast', 'fishing', 'awaiting_reel',
                          'awaiting_dynamite', 'banned')
            ),
            eligible_lines_seen INTEGER NOT NULL DEFAULT 0
                CHECK (eligible_lines_seen >= 0),
            next_cast_line INTEGER NOT NULL CHECK (next_cast_line > 0),
            cast_at INTEGER,
            reel_after INTEGER,
            command_sent_at INTEGER,
            banned_until INTEGER,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (bot_id, network, channel)
        );
        CREATE INDEX fishing_state_due_idx
            ON fishing_state(bot_id, network, phase, reel_after, banned_until);
        """
    )


async def migration_016(db: aiosqlite.Connection) -> None:
    await db.executescript(
        """
        CREATE TABLE bot_runtime_control (
            bot_id INTEGER PRIMARY KEY REFERENCES bots(id) ON DELETE CASCADE,
            response_enabled INTEGER NOT NULL DEFAULT 1
                CHECK (response_enabled IN (0, 1)),
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE admin_events (
            id INTEGER PRIMARY KEY,
            bot_id INTEGER NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
            event_type TEXT NOT NULL,
            message TEXT NOT NULL,
            source_message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            delivered_at TEXT,
            UNIQUE(bot_id, event_type, source_message_id)
        );
        CREATE INDEX admin_events_delivery_idx
            ON admin_events(bot_id, delivered_at, id);
        CREATE TABLE admin_api_credentials (
            bot_id INTEGER PRIMARY KEY REFERENCES bots(id) ON DELETE CASCADE,
            token TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )


async def migration_017(db: aiosqlite.Connection) -> None:
    await db.executescript(
        """
        CREATE TABLE bot_aliases (
            bot_id INTEGER NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
            alias TEXT NOT NULL,
            alias_key TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (bot_id, alias_key)
        );
        CREATE INDEX bot_aliases_lookup_idx ON bot_aliases(bot_id, alias_key);
        """
    )


async def migration_018(db: aiosqlite.Connection) -> None:
    await db.execute(
        "ALTER TABLE irc_profiles ADD COLUMN alternate_nicks TEXT NOT NULL DEFAULT '[]'"
    )


async def migration_019(db: aiosqlite.Connection) -> None:
    await db.executescript(
        """
        CREATE TABLE emergency_alert_state (
            bot_id INTEGER NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
            network TEXT NOT NULL,
            channel TEXT NOT NULL,
            last_alert_at INTEGER NOT NULL,
            PRIMARY KEY (bot_id, network, channel)
        );
        """
    )


async def migration_020(db: aiosqlite.Connection) -> None:
    await db.executescript(
        """
        CREATE TABLE maintenance_events (
            id INTEGER PRIMARY KEY,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            details TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TRIGGER maintenance_events_no_update
        BEFORE UPDATE ON maintenance_events BEGIN
            SELECT RAISE(ABORT, 'maintenance events are append-only');
        END;
        CREATE TRIGGER maintenance_events_no_delete
        BEFORE DELETE ON maintenance_events BEGIN
            SELECT RAISE(ABORT, 'maintenance events are append-only');
        END;
        """
    )


async def migration_021(db: aiosqlite.Connection) -> None:
    await db.executescript(
        """
        ALTER TABLE llm_profiles ADD COLUMN frequency_penalty REAL NOT NULL DEFAULT 0.0
            CHECK (frequency_penalty BETWEEN -2.0 AND 2.0);
        ALTER TABLE llm_profiles ADD COLUMN presence_penalty REAL NOT NULL DEFAULT 0.0
            CHECK (presence_penalty BETWEEN -2.0 AND 2.0);
        """
    )


async def migration_022(db: aiosqlite.Connection) -> None:
    await db.executescript(
        """
        CREATE TABLE anti_repeat_state (
            bot_id INTEGER NOT NULL REFERENCES bots(id) ON DELETE CASCADE,
            network TEXT NOT NULL,
            channel TEXT NOT NULL,
            flag_for_next_prompt INTEGER NOT NULL DEFAULT 0 CHECK (flag_for_next_prompt IN (0, 1)),
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (bot_id, network, channel)
        );
        """
    )


async def migration_023(db: aiosqlite.Connection) -> None:
    await db.executescript(
        """
        CREATE TABLE bot_lives_state (
            bot_id INTEGER PRIMARY KEY REFERENCES bots(id) ON DELETE CASCADE,
            current_activity TEXT NOT NULL,
            chosen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            expires_at TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )


async def migration_024(db: aiosqlite.Connection) -> None:
    await db.execute(
        "ALTER TABLE bots ADD COLUMN timezone TEXT NOT NULL DEFAULT 'UTC'"
    )


async def migration_025(db: aiosqlite.Connection) -> None:
    await db.executescript(
        """
        CREATE TABLE mood_state (
            bot_id INTEGER PRIMARY KEY REFERENCES bots(id) ON DELETE CASCADE,
            valence REAL NOT NULL CHECK (valence BETWEEN -1.0 AND 1.0),
            irritability REAL NOT NULL CHECK (irritability BETWEEN -1.0 AND 1.0),
            interaction_heat REAL NOT NULL DEFAULT 0.0 CHECK (interaction_heat >= 0.0),
            last_interaction_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_event TEXT NOT NULL DEFAULT 'initial' CHECK (
                last_event IN ('initial', 'interaction')
            ),
            last_valence_delta REAL NOT NULL DEFAULT 0.0,
            last_irritability_delta REAL NOT NULL DEFAULT 0.0
        );
        """
    )



async def migration_026(db: aiosqlite.Connection) -> None:
    await db.execute(
        "ALTER TABLE irc_profiles ADD COLUMN quit_message TEXT NOT NULL DEFAULT 'Restarting — back soon.'"
    )

MIGRATIONS: tuple[Migration, ...] = (
    migration_001, migration_002, migration_003, migration_004, migration_005,
    migration_006, migration_007, migration_008, migration_009, migration_010,
    migration_011, migration_012, migration_013, migration_014, migration_015,
    migration_016,
    migration_017,
    migration_018,
    migration_019,
    migration_020,
    migration_021,
    migration_022,
    migration_023,
    migration_024,
    migration_025,
    migration_026,
)


async def migrate(db: aiosqlite.Connection) -> None:
    await db.execute("PRAGMA foreign_keys = ON")
    await db.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations "
        "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    row = await (await db.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations")).fetchone()
    current = int(row[0]) if row else 0
    for version, migration in enumerate(MIGRATIONS, start=1):
        if version <= current:
            continue
        await migration(db)
        await db.execute("INSERT INTO schema_migrations(version) VALUES (?)", (version,))
        await db.commit()
