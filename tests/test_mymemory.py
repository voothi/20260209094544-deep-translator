#!/usr/bin/env python

"""Tests for `deep_translator` package."""

import pytest
from unittest.mock import patch, MagicMock, Mock
import json
from deep_translator import MyMemoryTranslator, exceptions


@pytest.fixture
def mymemory():
    return MyMemoryTranslator(source="en-GB", target="fr-FR")


def test_content(mymemory):
    """Sample pytest test function with the pytest fixture as an argument."""
    # from bs4 import BeautifulSoup
    # assert 'GitHub' in BeautifulSoup(response.content).title.string
    assert mymemory.translate(text="good") is not None


def test_inputs():
    with pytest.raises(exceptions.InvalidSourceOrTargetLanguage):
        MyMemoryTranslator(source="", target="")

    with pytest.raises(exceptions.InvalidSourceOrTargetLanguage):
        MyMemoryTranslator(source="auto", target="")

    with pytest.raises(exceptions.InvalidSourceOrTargetLanguage):
        MyMemoryTranslator(source="", target="en-GB")

    m1 = MyMemoryTranslator("en-GB", "fr-FR")
    m2 = MyMemoryTranslator("english", "french")
    assert m1._source == m2._source
    assert m1._target == m2._target


def test_payload(mymemory):
    with pytest.raises(exceptions.NotValidPayload):
        mymemory.translate(text={})

    with pytest.raises(exceptions.NotValidPayload):
        mymemory.translate(text=[])

    with pytest.raises(exceptions.NotValidLength):
        mymemory.translate(text="a" * 501)

@patch("deep_translator.mymemory.MyMemoryTranslator._http_get")
def test_mymemory_resilience_routing(mock_http_get, mymemory):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "responseData": {"translatedText": "bonjour"}
    }
    mock_http_get.return_value = mock_resp

    res = mymemory.translate(text="hello")
    assert res == "bonjour"
    mock_http_get.assert_called_once()
    kwargs = mock_http_get.call_args[1]
    assert kwargs["check_response"] == mymemory._check_mymemory_body

@patch("deep_translator.mymemory.MyMemoryTranslator._http_get")
def test_mymemory_empty_data_raises_translation_not_found(mock_http_get, mymemory):
    # Empty data body
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {}
    mock_http_get.return_value = mock_resp

    with pytest.raises(exceptions.TranslationNotFound):
        mymemory.translate("hello")

@patch("deep_translator.mymemory.MyMemoryTranslator._http_get")
def test_mymemory_exhaustion_maps_to_translation_not_found(mock_http_get, mymemory):
    from deep_translator.net import TransientResponseError
    mock_http_get.side_effect = TransientResponseError("Exhausted", response=MagicMock())

    with pytest.raises(exceptions.TranslationNotFound):
        mymemory.translate("hello")

def test_mymemory_ahk_tokenization_round_trip(mymemory):
    resp = MagicMock()
    resp.text = '{"responseData": {"translatedText": "hello [[S]] backslash [[B]] newline [[N]]"}}'
    resp.json.return_value = json.loads(resp.text)
    # Should not raise error
    mymemory._check_mymemory_body(resp)

    resp_trunc = MagicMock()
    resp_trunc.text = '{"responseData": {"translatedText": "hello [[S]] backslash [[B]] newline [[N"'
    # Truncated text -> raises TransientResponseError
    from deep_translator.net import TransientResponseError
    with pytest.raises(TransientResponseError):
        mymemory._check_mymemory_body(resp_trunc)
