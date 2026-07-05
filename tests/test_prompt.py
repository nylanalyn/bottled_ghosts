from cellar.prompt import build_prompt


def test_system_message_carries_rules_and_soul() -> None:
    result = build_prompt(soul="Be spectral.", module_state=[], memories=[],
                          dreams=[], relevant=[], history=[], speaker="bob", body="hi",
                          bot_nicks=("ghost",))
    assert result[0]["role"] == "system"
    assert "IRC character" in result[0]["content"]
    assert result[0]["content"].endswith("Be spectral.")


def test_context_blocks_and_current_message_land_in_final_user_turn() -> None:
    result = build_prompt(
        soul="Be spectral.", module_state=["IRC location: test #cellar"],
        memories=["preference: Likes tea"],
        dreams=["Yesterday: the telescope was repaired"],
        relevant=[("eve", "earlier ghost")],
        history=[("ada", "hello")],
        speaker="bob", body="ghost?", bot_nicks=("ghost",),
    )
    # System message first; everything else is at least one user turn.
    assert result[0]["role"] == "system"
    final = result[-1]
    assert final["role"] == "user"
    content = final["content"]
    # Context blocks retain their order within the final user turn, and the
    # current message comes last.
    assert content.index("IRC location: test #cellar") < content.index("preference: Likes tea")
    assert content.index("preference: Likes tea") < content.index("Yesterday: the telescope was repaired")
    assert content.index("Yesterday: the telescope was repaired") < content.index("<eve> earlier ghost")
    assert content.index("<eve> earlier ghost") < content.index("ghost?")
    assert content.endswith("ghost?")


def test_bot_history_lines_become_assistant_turns() -> None:
    # The bot's own prior lines must land in the assistant role so the model
    # sees its voice as dialogue rather than text to imitate. Other speakers
    # remain in user turns as <nick> text.
    result = build_prompt(
        soul="Be spectral.", module_state=[], memories=[], dreams=[], relevant=[],
        history=[
            ("ada", "hi ghost"),
            ("ghost", "hey ada"),
            ("ada", "you always say that"),
        ],
        speaker="ada", body="see?", bot_nicks=("ghost",),
    )
    roles = [m["role"] for m in result]
    # system, user (ada), assistant (ghost), user (ada + current message merged)
    assert roles == ["system", "user", "assistant", "user"]
    assert result[1]["content"] == "<ada> hi ghost"
    assert result[2]["content"] == "hey ada"  # no <nick> prefix on bot's own lines
    assert "<ada> you always say that" in result[3]["content"]
    assert result[3]["content"].endswith("see?")


def test_consecutive_same_role_history_merges() -> None:
    # Two human lines back-to-back should merge into one user turn rather than
    # producing adjacent user messages.
    result = build_prompt(
        soul="Be spectral.", module_state=[], memories=[], dreams=[], relevant=[],
        history=[("ada", "one"), ("eve", "two")],
        speaker="ada", body="three", bot_nicks=("ghost",),
    )
    roles = [m["role"] for m in result]
    assert roles == ["system", "user"]
    assert "<ada> one" in result[1]["content"]
    assert "<eve> two" in result[1]["content"]
    assert result[1]["content"].endswith("three")


def test_collided_configured_nick_is_not_attributed_to_active_bot() -> None:
    result = build_prompt(
        soul="Be spectral.", module_state=[], memories=[], dreams=[], relevant=[],
        history=[
            ("ghost", "I own the configured nick"),
            ("ghost_", "I am the active bot"),
        ],
        speaker="alice", body="hello", bot_nicks=("ghost_",),
    )
    assert result[1] == {"role": "user", "content": "<ghost> I own the configured nick"}
    assert result[2] == {"role": "assistant", "content": "I am the active bot"}
