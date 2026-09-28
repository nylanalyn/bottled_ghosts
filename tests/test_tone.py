from cellar.tone import split_tone, tone_instruction


def test_tone_tag_is_stripped_and_parsed_per_speaker() -> None:
    cleaned, ratings = split_tone(
        "ha, fair enough\nsee you later\n[tone: Alice=warm, bob=hostile]",
        ("alice", "Bob"),
    )
    assert cleaned == "ha, fair enough\nsee you later"
    assert ratings == {"alice": "warm", "bob": "hostile"}


def test_single_speaker_may_omit_the_nick() -> None:
    assert split_tone("ok [TONE: Cold.]", ("carol",)) == ("ok", {"carol": "cold"})


def test_unknown_nicks_and_labels_are_ignored_but_still_stripped() -> None:
    cleaned, ratings = split_tone(
        "sure\n[tone: mallory=warm, carol=furious, dave=hostile]", ("carol", "dave"),
    )
    assert cleaned == "sure"
    assert ratings == {"dave": "hostile"}


def test_tags_are_stripped_even_when_nobody_was_rated() -> None:
    assert split_tone("[tone: warm] hello", ()) == ("hello", {})


def test_instruction_names_every_rated_speaker() -> None:
    text = tone_instruction(("alice", "bob"))
    assert "[tone: alice=neutral, bob=neutral]" in text
    assert "hostile" in text
