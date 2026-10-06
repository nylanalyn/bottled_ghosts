# Bottled Ghosts

The v0.1 runtime connects one configured Bottle to IRC, logs messages to SQLite,
calls an OpenAI-compatible chat endpoint when its nick is mentioned, sanitizes the
response, and sends it with hard output limits. Characters may emit real IRC
actions by beginning a generated line with `/me `; the runtime converts that
line to CTCP `ACTION` without bypassing normal limits or cooldowns.

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

For automatic conversational continuity, enable recollections for a Bottle:

```bash
bottled-ghosts recollections-mode 1 on
bottled-ghosts recollect 1
bottled-ghosts recollections 1
bottled-ghosts recollection-sources RECOLLECTION_ID
bottled-ghosts recollection-archive RECOLLECTION_ID --actor aureate
```

Schedule `recollect 1` about every ten minutes with your preferred timer or
cron service, using the same `--database` path as the runtime. The job processes
only closed conversation chunks, records empty results, and can be run again
without duplicate recollections. A run handles at most ten chunks by default;
use `--limit-chunks` to catch up. Recollection mode disables per-reply sediment
extraction for that Bottle and starts with new messages. Restart the Bottle after changing modes. Recollections
are fallible summaries, retrieved only within the same Bottle and conversation;
private messages are not recalled into public rooms.
Recollections and dreams skip `[Fishing]`, `[Hunt]`, `[Banter]`, and `[Karma]`
announcements and command-shaped `!word` lines when preparing long-term summaries,
including those pasted inside another message. Those lines
remain in the message log and recent live conversation, and recollection source
links still show the full original chunk. A human plan discussed in prose can
still be remembered. Existing summaries are unchanged.
The recollection model makes an explicit keep/discard decision. Discarded chunks,
and generated summaries that contradict that decision by concluding there was no
lasting continuity, are recorded as empty processed chunks instead of searchable
recollections. Transient health complaints and speculation are also excluded.
An addressed `remember that ...` or `remember this: ...` request still creates
one pending semantic candidate for operator review. Ordinary conversation does
not create semantic candidates in recollection mode. A request never becomes a
trusted memory merely because someone phrased it as a command.

For this checkout, a user systemd timer is included:

```bash
mkdir -p ~/.config/systemd/user
cp bottled-ghosts-recollect.service bottled-ghosts-recollect.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now bottled-ghosts-recollect.timer
```

The timer calls `recollect-all` every ten minutes for enabled Bottles in
recollection mode. Adjust the service paths if the checkout or database moves.

Sediment extraction is disabled by default because it adds a second LLM call
after each handled message. Enable it explicitly for a Bottle:

```bash
bottled-ghosts memory-extraction 1 on
```

The extractor may write categorized candidates to SQLite. New facts remain
pending and are not used as trusted memory; exact repeats of an existing active
memory are attached automatically as additional evidence. Disable extraction
with the same command and `off`.

Review sediment and inspect approved memories with:

```bash
bottled-ghosts sediment-list
bottled-ghosts sediment-approve 1 --actor aureate
bottled-ghosts sediment-reject 2 --actor aureate
bottled-ghosts sediment-attach CANDIDATE_ID MEMORY_ID --actor aureate
bottled-ghosts sediment-bulk-approve --min-confidence 0.9
bottled-ghosts sediment-bulk-approve --min-confidence 0.9 --type preference --apply --actor aureate
bottled-ghosts sediment-auto-approve-repeats
bottled-ghosts sediment-expire-temporary
bottled-ghosts sediment-expire-temporary --apply --actor aureate
bottled-ghosts memories BOTTLE_ID USER_UUID
bottled-ghosts memory-evidence MEMORY_ID
bottled-ghosts memory-edit 1 --text "Prefers mature cheese" --actor aureate
bottled-ghosts memory-merge TARGET_ID SOURCE_ID... --actor aureate
bottled-ghosts memory-consolidate-scan BOTTLE_ID USER_UUID --actor aureate
bottled-ghosts consolidation-list
bottled-ghosts consolidation-accept PROPOSAL_ID --actor aureate
```

Exact repeats of an already approved memory automatically become additional
evidence, with `automatic:exact-repeat` recorded in the audit log. Similar
wording is never merged automatically: the explicit consolidation scan creates
persistent proposals for operator acceptance or rejection. The bulk approval
command previews its filtered selection by default and only changes SQLite
when `--apply` is supplied. Model confidence alone is not proof of a good
memory. Approval, rejection, evidence attachment, edits, and merges are
transactional and append audit events. Merged memories remain as archived
redirects, and every supporting candidate retains its source messages.
The temporary-candidate cleanup previews pending `temporary_state` candidates
older than 24 hours, then rejects them with an audit record only when `--apply`
is supplied. Use `--hours` to change the cutoff.
Sediment and approved memories belong to the Bottle that extracted them; the
dashboard displays that owner. Only that Bottle's approved memories are
retrieved into its prompts. Search raw logs with:

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

The optional `reflection` module gives a Bottle a short private planning pass
over its fully assembled prompt before public response generation. The notes
are ephemeral: they are not logged, remembered, or sent to IRC. Enable it for
one Bottle at a time while evaluating its effect:

```bash
bottled-ghosts module-toggle 1 reflection on --actor aureate
bottled-ghosts module-settings 1 reflection '{"max_tokens":160,"temperature":0.2}' --actor aureate
```

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

The same loopback service exposes an unauthenticated `GET /health` for local
monitoring. It returns HTTP 200 while the Bottle is connected to IRC with no
failed modules, or HTTP 503 with JSON details otherwise. Configure one Uptime
Kuma HTTP monitor per Bottle, using its unique admin API port. Kuma must run on
the host network (or directly on the host), because the endpoint intentionally
does not bind beyond loopback.

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

Let a Bottle play RustJeeves' fishing game with the optional fishing module. It
sends one `!recast` a day (reel in, cast straight back out) at a random time
20–23.5 hours after the last one, on the next message in a configured channel.
Shortly before each recast, a small LLM call with the Bottle's soul lets it
choose a plain recast, a lure, chum, or both, and occasionally a stick of
`!dynamite` afterwards. Dynamite is offered at most once per
`dynamite_cooldown_days` (default 14, minimum 8), longer than Jeeves' 7-day
hand regrowth, so a Bottle never loses both hands. A failed choice falls back
to a plain `!recast`. Jeeves' latest reply is kept in `fishing_schedule` and
shown to the Bottle when the channel talks about fishing:

```bash
bottled-ghosts module-toggle 1 fishing on --actor aureate
bottled-ghosts module-settings 1 fishing '{"channels":["#lobby"],"game_nick":"Jeeves","min_recast_hours":20,"max_recast_hours":23.5,"ai_choices":true,"allow_dynamite":true,"dynamite_cooldown_days":14}' --actor aureate
```

The choice uses the `initiative` task model (see `task-model`). Set
`"ai_choices": false` for plain recasts with no LLM call. A Bottle whose
`ignore` patterns drop Jeeves' fishing lines still fishes, but cannot see its
results or notice a ban.

The optional hugs module uses RustJeeves' `!hug <nick>` and the affinity
module's warmth scores (enable `affinity` too; without it every warmth is
neutral and the module does nothing). When someone the Bottle is genuinely glad
to see (`min_warmth`, default 0.55) speaks, a small `chance` (default 0.05)
plans a hug sent 2–10 minutes later on the next channel message. It hugs at
most once per `cooldown_hours` (default 24) and the same person at most once
per `person_cooldown_days` (default 7). When someone hugs the Bottle, it sends
`!reject` only if their warmth is at or below `reject_below` (default -0.25).
Plans are in `hug_plans`; every hug given, accepted, or rejected is in
`hug_events`:

```bash
bottled-ghosts module-toggle 1 hugs on --actor aureate
bottled-ghosts module-settings 1 hugs '{"channels":["#lobby"],"game_nick":"Jeeves","chance":0.05}' --actor aureate
```

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

When moods or affinity is enabled, an addressed reply also carries a hidden
exchange rating. The model ends its reply with `[tone: nick=warm]` (one of
`warm`, `neutral`, `cold`, `hostile` per person who addressed it), and the
runtime always strips that line before anything is sent. Affinity warms or
cools each rated person's score, and moods shift valence by the average
rating and irritability by the harshest one. Tune how strongly a Bottle's
mood reacts with `tone_sensitivity` (0 to 3, default 1; 0 turns it off):

```bash
bottled-ghosts module-settings 1 moods '{"profile":"aria","tone_sensitivity":0.7}' --actor aureate
```

A missing rating is logged and changes nothing. See ADR-020.

Bottles in recollection mode also keep self-memories: while summarizing a
public conversation, the recollection job records up to three lasting things
the Bottle said about itself (tastes, opinions, its own projects, how it feels
about someone, promises). They are stored without review and retrieved into
later prompts as the Bottle's own earlier words, so it stays consistent.
Private conversations never produce them. Inspect or retire them with:

```bash
bottled-ghosts self-memories 1
bottled-ghosts self-memory-archive SELF_MEMORY_ID --actor aureate
```

Give a Bottle relationship notes with the optional relationships module. A
note enters the prompt only when that person is speaking, spoke recently in
the room, or is mentioned, so relationships show up in the moment instead of
in every reply. Notes work for other Bottles and for people:

```bash
bottled-ghosts module-settings 1 relationships '{"people":{"frauderick":"your grumpy friend; you tease him about Arch and he pretends to hate it","bork":"the pug; impossible not to like"}}' --actor aureate
bottled-ghosts module-toggle 1 relationships on --actor aureate
```

`module-settings` replaces the whole JSON, so to add, change, or remove one
person without retyping everyone else, use:

```bash
bottled-ghosts relationships 1
bottled-ghosts relationship-set 1 styx "a regular; you trade music recommendations" --actor aureate
bottled-ghosts relationship-remove 1 styx --actor aureate
```

Nicks match case-insensitively, so setting `bork` replaces an existing
`Bork` note. Changes are audited like `module-settings`; restart the Bottle
to apply them.

Optional `lookback_lines` (default 15) controls how far back "spoke
recently" reaches. How each relationship is going day to day comes from
affinity.

Let a Bottle break a lull with the optional initiative module. While
connected, the runtime checks each channel once a minute. The module offers
the Bottle one opening only after a randomized quiet period, only if a human
spoke within `human_recent_hours`, only if a human has spoken since the
Bottle's last opener there, and at most `max_per_day` times per channel in 24
hours. Lines from Bottles in this database, `other_bots`, and ambient chat's
`utility_bot_nicks` never count as human. The model may still decline with
`[pass]`, and openers obey every normal output limit, quiet mode, `off`,
dream sleep, and mood breaks. Pending follow-up threads make natural openers:

```bash
bottled-ghosts module-settings 1 initiative '{"min_quiet_minutes":45,"max_quiet_minutes":180,"max_per_day":3,"human_recent_hours":12,"channels":["#fractalsignal"]}' --actor aureate
bottled-ghosts module-toggle 1 initiative on --actor aureate
```

Every offer and whether the Bottle spoke or passed is recorded in the
`initiative_events` table. See ADR-022.

Each kind of LLM call can use its own model while keeping the Bottle's
endpoint, API key, and that call's own temperature and token budget. Tasks are
`reply`, `initiative` (falls back to `reply`), `reflection`, `extraction`,
`recollection`, `dream`, `followup`, `summary`, and `consolidation`; anything
unset uses the Bottle's main model. Use the model names your OpenAI-compatible
endpoint (for example LiteLLM) exposes:

```bash
bottled-ghosts task-model 1 recollection deepseek-v4-flash --actor aureate
bottled-ghosts task-model 1 dream glm-5.3 --actor aureate
bottled-ghosts task-model 1 dream --clear --actor aureate
bottled-ghosts task-models 1
```

Background jobs pick up changes on their next run; restart a Bottle for reply,
reflection, and initiative changes. The admin `status` and `model` commands
list any overrides. See ADR-023.

Dreaming is an explicit job rather than a hidden background scheduler:

```bash
bottled-ghosts dream 1 --hours 24
bottled-ghosts dream-all --hours 24
bottled-ghosts dreams 1
```

Each summary records its exact period in SQLite, invokes enabled modules'
`nightly` hooks, and becomes retrieval context for later replies. Dream input
includes public-channel messages only. Older summaries may contain private
messages, so they remain inspectable with `dreams` but are excluded from reply
prompts and follow-up prompts; newly generated dreams are marked public-safe.
For automatic
nightly operation, install the per-Bottle timers included in this repository:

```bash
mkdir -p ~/.config/systemd/user
cp aria-dream.service aria-dream.timer frauderick-dream.service \
   frauderick-dream.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now aria-dream.timer frauderick-dream.timer
systemctl --user list-timers '*-dream.timer'
```

Aria dreams at 03:00 and Frauderick at 03:30 in the host's local timezone.
Those services use `dream --sleep`: the Bottle remains connected but full
responses and module-generated IRC commands are disabled during the dream, and
the previous response state is restored afterward, including when the LLM call
fails. Rumi-as, Bork, and disabled Bottles are not included in these timers.

Open the operational dashboard with:

```bash
bottled-ghosts tui --actor aureate
```

The dashboard shows configured Bottles, memory extraction state, pending
sediment, enabled modules, last activity, and recent messages. Use the arrow
keys to select a Bottle, `F7` to explicitly start or stop it, `r` to refresh,
and `q` to quit. Closing the TUI stops Bottles launched by that TUI.
The Sediment tab shows candidate provenance and likely existing memories from
the same Bottle/user scope. Press `a` to approve the selected candidate, `x` to
reject it, or enter a memory ID to attach it as further evidence; all actions
use the supplied audit identity.
The Recollections tab shows fallible conversation summaries and their source
messages, and lets an operator archive a mistaken summary. The Memories tab
lists trusted memories and every supporting candidate/source.
Edit the selected
memory's text, type, or confidence and press the save button or `Ctrl+S`; the
change is written transactionally with the same audit identity.
The Consolidation tab reviews persistent merge proposals. Acceptance moves all
evidence to one canonical memory and archives the redundant rows; rejection
records the decision without changing trusted memory. Proposal generation is
only run by the explicit `memory-consolidate-scan` CLI command.
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
