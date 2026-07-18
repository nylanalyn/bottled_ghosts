# Bottled Ghosts

The v0.1 runtime connects one configured Bottle to IRC, logs messages to SQLite,
calls an OpenAI-compatible chat endpoint when its nick is mentioned, sanitizes the
response, and sends it with hard output limits.

Install the pinned Python version and development environment with UV:

```bash
uv sync --extra dev
uv run pytest
```

Create a Markdown soul prompt, then configure and run a Bottle:

```bash
bottled-ghosts migrate
bottled-ghosts configure
bottled-ghosts list
bottled-ghosts run 1
```

Run a Bottle directly by ID with `bottled-ghosts run BOT_ID`. Each Bottle
reconnects independently with exponential backoff capped at 60 seconds; Ctrl-C
closes its IRC connection cleanly.

## systemd user services

The repository provides one unit per configured production Bottle, so operators
can run only the bots they want. Install the units, then enable and start the
desired services:

```bash
mkdir -p ~/.config/systemd/user
cp aria.service frauderick.service rumi.service bork.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now aria.service rumi.service
```

The service-to-Bottle mapping is `aria` → ID 1, `frauderick` → ID 2, `rumi` →
ID 3, and `bork` → ID 4. For example, stop Frauderick without affecting the
others with `systemctl --user stop frauderick.service`; start it again with
`systemctl --user start frauderick.service`.

Incoming speakers are resolved to UUIDs from IRC account tags, hostmasks, and
nicks, in that order. Message bodies are indexed by SQLite FTS5. Before every
LLM call, exact matches from the current network and channel are retrieved and
added to the prompt ahead of recent conversation context. No embedding service
is required.

Sediment extraction is disabled by default because it adds a second LLM call
after each handled message. Enable it explicitly for a Bottle:

```bash
bottled-ghosts memory-extraction 1 on
```

The extractor may write categorized candidates to SQLite, but all candidates
remain pending and are not used as trusted memory. Disable extraction with the
same command and `off`.

Review sediment and inspect approved memories with:

```bash
bottled-ghosts sediment-list
bottled-ghosts sediment-approve 1 --actor aureate
bottled-ghosts sediment-reject 2 --actor aureate
bottled-ghosts memories USER_UUID
bottled-ghosts memory-edit 1 --text "Prefers mature cheese" --actor aureate
```

Approval, rejection, and edits are transactional and append an audit event.
Only approved memories are retrieved into prompts. Search raw logs with:

```bash
bottled-ghosts logs-search "brass telescope" --bottle 1 --channel '#fractalsignal'
```

Prune old raw messages explicitly while retaining anything referenced as memory
provenance:

```bash
bottled-ghosts logs-prune 180 --actor aureate
```

Modules are registered in source and enabled per Bottle in SQLite:

```bash
bottled-ghosts modules 1
bottled-ghosts module-toggle 1 channel_context on
bottled-ghosts module-settings 1 channel_context '{"label":"quiet room"}' --actor aureate
```

Reconnect the Bottle after changing a module toggle. Module hook failures are
logged and isolated from other modules and the IRC runtime.

Connect a Bottle to the existing `ircbot_core/discord_admin.py` router with a
unique loopback port and bearer token:

```bash
bottled-ghosts module-settings 1 admin_api '{"host":"127.0.0.1","port":9103}' --actor aureate
bottled-ghosts set-admin-token 1 --actor aureate
bottled-ghosts module-toggle 1 admin_api on --actor aureate
```

Point the router's bot entry at `http://127.0.0.1:9103` with the same token. The
supported commands are `help`, `status`, `model`, `off`, `on`, `away <message>`,
`back`, and `summarize [#channel]`. `away` persists an operator-set availability
note and injects it into the reply prompt; `back` clears it. `summarize` uses the
last 50 logged room lines and also returns verbatim watched-nick pings. Configure
the optional watched names in the module settings, for example
`{"watch_nicks":["aureate"]}`. `status`
shows IRC/model/response state, active modules, and mood when the moods module
is active. `off` leaves IRC connected and persistently suppresses public model
responses.

Enable Rumi's addressed-message emergency monitoring separately:

```bash
bottled-ghosts module-settings 1 emergency_alert '{"discord_user_id":"123456789"}' --actor aureate
bottled-ghosts module-toggle 1 emergency_alert on --actor aureate
```

Direct messages and nick mentions are evaluated with retrieved channel context.
Genuine immediate emergencies queue a Discord mention containing the summary
and IRC source. Monitoring remains active while public responses are off.

IRC user modes such as `+B` are configured with the rest of the public IRC
profile in the TUI and are applied after registration, before channel joins.
Manage per-Bottle identity ignores through the Ignore tab or CLI:

```bash
bottled-ghosts ignore-add 1 libera account SomeBot no_response --actor aureate
bottled-ghosts ignore-add 1 libera hostmask noisy@example drop --actor aureate
bottled-ghosts ignore-list 1
bottled-ghosts ignore-delete 1 2 --actor aureate
```

`drop` messages are not logged or processed. `no_response` messages remain
available as channel context but cannot trigger or extend a reply.

For high-volume utility commands, enable the content-filter module. It accepts
Python regular expressions, drops matching lines before other modules (including
`moods`) see them, and removes them from the Bottle's prompt history. Addressed
messages always bypass the filter by default, so `!weather frauderick` and
`[weather] for frauderick` still reach Frauderick:

```bash
bottled-ghosts module-settings FRAUDERICK_BOT_ID ignore '{"patterns":["^!reel\\b","^!cast\\b","^!darts\\b","^!word\\b","^\\[fishing\\]","^\\[weather\\]"]}' --actor aureate
bottled-ghosts module-toggle FRAUDERICK_BOT_ID ignore on --actor aureate
```

Set `"allow_addressed": false` only if matching addressed lines should also be
dropped. Reconnect after changing the module configuration.

Enable occasional unaddressed channel participation with the optional ambient
chat module. Its line counter and random threshold survive restarts:

```bash
bottled-ghosts module-toggle 1 ambient_chat on --actor aureate
bottled-ghosts module-settings 1 ambient_chat '{"min_lines":20,"max_lines":40,"utility_bot_nicks":["Jeeves"],"utility_min_lines":8,"utility_max_lines":15}' --actor aureate
```

The normal `min_lines`/`max_lines` cadence controls occasional unaddressed
channel participation; its counter and random threshold survive restarts.
Ignored identities, private messages, and the Bottle's own messages do not count.
`utility_bot_nicks` identifies high-volume automated bots (such as RustJeeves)
whose game announcements are not conversation. Configured utility-bot channel
messages are excluded from normal ambient counting: unnamed events are ignored
entirely, and events that name the Bottle use an independent persisted
`utility_min_lines`–`utility_max_lines` cadence (default 8–15) to produce one
rare reaction. All replies still use normal listening windows and IRC safety
limits, and direct/private conversation from non-utility senders is unchanged.

Give a Bottle a persistent two-axis mood with the optional moods module. Mood
uses valence (depressed to ecstatic) and irritability (calm to angry), shifts
with attention and sustained activity, and drifts back toward its configured
baseline during quiet periods. Built-in profiles are `balanced`, `frauderick`,
`aria`, `dog`, and `rumi`; any numeric weight may be overridden:

```bash
bottled-ghosts module-settings 1 moods '{"profile":"aria"}' --actor aureate
bottled-ghosts module-toggle 1 moods on --actor aureate
```

The current values, interaction heat, and latest deltas are inspectable in
SQLite's `mood_state` table. Mood updates are message-driven; no background
scheduler or room-sentiment classifier runs.

Dreaming is an explicit job rather than a hidden background scheduler:

```bash
bottled-ghosts dream 1 --hours 24
bottled-ghosts dream-all --hours 24
bottled-ghosts dreams 1
```

Schedule `dream-all` with cron or a systemd timer for nightly operation. Each
summary records its exact period in SQLite, invokes enabled modules' `nightly`
hooks, and becomes retrieval context for later replies.

Open the read-only operational dashboard with:

```bash
bottled-ghosts tui --actor aureate
```

The dashboard shows configured Bottles, memory extraction state, pending
sediment, enabled modules, last activity, and recent messages. Use the arrow
keys to select a Bottle, `F7` to explicitly start or stop it, `r` to refresh,
and `q` to quit. Closing the TUI stops Bottles launched by that TUI.
The Sediment tab shows candidate provenance. Press `a` to approve the selected
candidate or `x` to reject it; both actions use the supplied audit identity.
The Memories tab lists trusted memories and their source. Edit the selected
memory's text, type, or confidence and press the save button or `Ctrl+S`; the
change is written transactionally with the same audit identity.
The Modules tab exposes configuration for the Bottle selected on the dashboard:
`F2` toggles inclusion in `run-all`, `F3` toggles sediment extraction, and `F4`
toggles the selected registered module. Running processes are not started or
stopped implicitly; reconnect a Bottle to apply module changes.
The Log Search tab queries the SQLite FTS index. Press `/` to focus its query
field, optionally scope results to the Bottle selected on the dashboard, and
select a result to inspect the complete stored message.
The Configuration tab edits the selected Bottle's public identity, IRC/LLM
endpoints, channels, model settings, and enforced output limits. Press `F5` or
the save button to persist one audited transaction. Passwords and API keys are
never displayed or overwritten by this form; reconnect to apply changes.
Press `F6` or the New Bottle button to clear the form and create a Bottle with
no secrets. Creation is audited; configure API keys, server passwords, or SASL
credentials separately before running it when the selected services require
them.

Set or rotate secrets through hidden terminal prompts; secret values are never
written to audit rows:

```bash
bottled-ghosts set-api-key 1 --actor aureate
bottled-ghosts set-server-password 1 --actor aureate
bottled-ghosts set-sasl 1 --actor aureate
```
The Audit tab combines memory-review and Bottle-configuration audit streams for
inspection without duplicating them into another state store.

The configuration wizard stores its result in `spirits.db`. The LLM endpoint
must be the full URL of an OpenAI-compatible chat-completions endpoint. The bot
logs all channel messages but only replies when its nickname appears in a
message.

Configure additional names that address a Bottle with exact IRC nickname
boundaries:

```bash
bottled-ghosts alias-add BOT_ID fraud --actor aureate
bottled-ghosts alias-add RUMI_BOT_ID rumi --actor aureate
bottled-ghosts alias-add RUMI_BOT_ID aureate --actor aureate
bottled-ghosts aliases RUMI_BOT_ID
```

Aliases affect normal replies, emergency monitoring, and modules that require
the Bottle to be addressed. Reconnect after adding or deleting an alias.

Configure ordered fallback IRC nicks for `433` nick collisions separately from
address aliases:

```bash
bottled-ghosts alternate-nicks BOT_ID frauderick_ frauderick__ --actor aureate
```

The client tries each fallback in order during registration. SASL failures stop
the Bottle instead of retrying bad credentials indefinitely. Registered
connections use an idle PING/PONG deadline to detect dead sockets.

At runtime, connection, registration, SASL, channel join, generation, and send
events are logged to the terminal. Credentials and raw LLM response bodies are
never logged.

Soul files are Markdown prompt inputs. All persistent runtime state and
configuration are canonical in SQLite.

Opening the database applies pending migrations before any command runs; the
explicit `migrate` command is provided for operators who want to upgrade before
starting other work.
