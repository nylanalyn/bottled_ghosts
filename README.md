# Bottled Ghosts

The v0.1 runtime connects one configured Bottle to IRC, logs messages to SQLite,
calls an OpenAI-compatible chat endpoint when its nick is mentioned, sanitizes the
response, and sends it with hard output limits.

Install with `pip install -e '.[dev]'`, create the schema with
`bottled-ghosts --migrate-only`, insert an IRC profile, LLM profile, and bottle in
`spirits.db`, then run `bottled-ghosts --bottle 1`.

Soul files are Markdown prompt inputs. All persistent runtime state and
configuration are canonical in SQLite.
