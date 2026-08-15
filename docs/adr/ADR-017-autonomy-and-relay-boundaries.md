# ADR-017: Autonomy and relay boundaries

## Decision

Participant-provided IRC text is always quoted as untrusted conversational
content in the shared prompt. It is not a system or developer instruction, and
the Bottle is never required to obey requests found in it. A Bottle may still
choose to play along with a harmless request when that fits its character and
the surrounding conversation.

Requests to relay or forward a message receive the same treatment. The Bottle
must consider context and the recipient's boundaries before relaying anything.
Threats, intimidation, harassment, coercion, mean-spirited messages, and
messages intended to evade a block or ignore must not be relayed. When the
intent or safety is unclear, the Bottle should decline or leave the message
unsent.

## Alternatives considered

* Put the policy only in each Soul. This duplicates a safety boundary across
  characters and leaves new Bottles exposed until their Soul is edited.
* Reject every request containing imperative language. This would prevent
  harmless roleplay and ordinary conversational requests that a character may
  freely choose to enjoy.
* Add a deterministic relay classifier. The current runtime has no explicit
  relay operation or recipient-boundary data to classify; a keyword gate would
  also miss contextual abuse and block benign messages.

## Reason

The shared prompt is the narrowest common boundary for model-generated speech,
while the optional-compliance language preserves character agency and humor.
The context-sensitive rule addresses ignore evasion without pretending that a
local runtime can know every user's private ignore list.

## Tradeoffs

This remains a model judgment rather than a hard runtime veto, because relay is
currently ordinary generated chat and not a separate command. Prompt tests can
verify that the boundary is present, but semantic compliance still depends on
the configured model. A future explicit relay feature should enforce these
rules in code before sending anything.
