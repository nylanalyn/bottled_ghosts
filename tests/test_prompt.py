from cellar.prompt import build_prompt


def test_prompt_layer_order() -> None:
    result = build_prompt(soul="Be spectral.", memories=["preference: Likes tea"],
                          relevant=[("eve", "earlier ghost")],
                          history=[("ada", "hello")],
                          speaker="bob", body="ghost?")
    assert "IRC character" in result[0]["content"]
    assert result[0]["content"].endswith("Be spectral.")
    assert result[1]["content"].index("preference: Likes tea") < result[1]["content"].index("<eve> earlier ghost")
    assert result[1]["content"].index("<eve> earlier ghost") < result[1]["content"].index("<ada> hello")
    assert result[1]["content"].index("<ada> hello") < result[1]["content"].index("ghost?")
