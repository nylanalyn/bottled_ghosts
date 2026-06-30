from cellar.prompt import build_prompt


def test_prompt_layer_order() -> None:
    result = build_prompt(soul="Be spectral.", module_state=["IRC location: test #cellar"],
                          memories=["preference: Likes tea"],
                          dreams=["Yesterday: the telescope was repaired"],
                          relevant=[("eve", "earlier ghost")],
                          history=[("ada", "hello")],
                          speaker="bob", body="ghost?")
    assert "IRC character" in result[0]["content"]
    assert result[0]["content"].endswith("Be spectral.")
    assert result[1]["content"].index("IRC location: test #cellar") < result[1]["content"].index("preference: Likes tea")
    assert result[1]["content"].index("preference: Likes tea") < result[1]["content"].index("Yesterday: the telescope was repaired")
    assert result[1]["content"].index("Yesterday: the telescope was repaired") < result[1]["content"].index("<eve> earlier ghost")
    assert result[1]["content"].index("<eve> earlier ghost") < result[1]["content"].index("<ada> hello")
    assert result[1]["content"].index("<ada> hello") < result[1]["content"].index("ghost?")
