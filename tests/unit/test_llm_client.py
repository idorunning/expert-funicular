import pytest

from brokerledger.categorize.llm_client import LLMError, OllamaClient, _parse_llm_json


def test_refuses_non_local_url():
    with pytest.raises(LLMError):
        OllamaClient(base_url="http://example.com:11434")


def test_parse_llm_json_valid():
    out = _parse_llm_json(
        '{"category":"Food","group":"discretionary","confidence":0.82,"reason":"tesco"}'
    )
    assert out.category == "Food"
    assert out.group == "discretionary"
    assert 0 <= out.confidence <= 1


def test_parse_llm_json_invalid_category():
    with pytest.raises(LLMError):
        _parse_llm_json('{"category":"Pizza","group":"discretionary","confidence":0.5}')


def test_parse_llm_json_self_heals_wrong_group():
    out = _parse_llm_json(
        '{"category":"Food","group":"committed","confidence":0.9,"reason":""}'
    )
    assert out.group == "discretionary"  # corrected


def test_parse_llm_json_with_prose():
    out = _parse_llm_json(
        'Here is my answer: {"category":"Water","group":"committed","confidence":0.95,"reason":""}'
    )
    assert out.category == "Water"


def test_parse_llm_json_captures_thinking_from_body():
    out = _parse_llm_json(
        '{"thinking":"Tesco is a UK supermarket so this is Food.",'
        '"category":"Food","group":"discretionary","confidence":0.8,"reason":"tesco"}'
    )
    assert out.thinking.startswith("Tesco is a UK supermarket")


def test_parse_llm_json_thinking_absent_is_empty_string():
    out = _parse_llm_json(
        '{"category":"Food","group":"discretionary","confidence":0.8,"reason":"x"}'
    )
    assert out.thinking == ""


def test_parse_llm_json_accepts_group_prefixed_category():
    # Some models echo the taxonomy line back as "group :: Category".
    out = _parse_llm_json(
        '{"category":"discretionary :: Food","group":"discretionary",'
        '"confidence":0.7,"reason":""}'
    )
    assert out.category == "Food"
