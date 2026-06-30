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
