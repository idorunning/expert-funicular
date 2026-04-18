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
