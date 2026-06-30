# Bottled Ghosts

The v0.1 runtime connects one configured Bottle to IRC, logs messages to SQLite,
calls an OpenAI-compatible chat endpoint when its nick is mentioned, sanitizes the
response, and sends it with hard output limits.

Install and test inside a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest
```

Create a Markdown soul prompt, then configure and run a Bottle:

```bash
bottled-ghosts migrate
bottled-ghosts configure
bottled-ghosts list
bottled-ghosts run 1
```

Run every enabled Bottle concurrently with:

```bash
bottled-ghosts run-all
```

Each Bottle reconnects independently with exponential backoff capped at 60
seconds. Ctrl-C cancels all Bottle tasks and closes their IRC connections.

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

Modules are registered in source and enabled per Bottle in SQLite:

```bash
bottled-ghosts modules 1
bottled-ghosts module-toggle 1 channel_context on
```

Reconnect the Bottle after changing a module toggle. Module hook failures are
logged and isolated from other modules and the IRC runtime.

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
keys to select a Bottle, `r` to refresh, and `q` to quit.
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

To add or replace SASL credentials on an existing Bottle:

```bash
bottled-ghosts set-sasl 1
```

The configuration wizard stores its result in `spirits.db`. The LLM endpoint
must be the full URL of an OpenAI-compatible chat-completions endpoint. The bot
logs all channel messages but only replies when its nickname appears in a
message.

At runtime, connection, registration, SASL, channel join, generation, and send
events are logged to the terminal. Credentials and raw LLM response bodies are
never logged.

Soul files are Markdown prompt inputs. All persistent runtime state and
configuration are canonical in SQLite.
