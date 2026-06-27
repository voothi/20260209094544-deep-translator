#!/usr/bin/env python

"""Tests for `deep_translator` package."""

import pytest
import urllib.error
from unittest.mock import patch, MagicMock, Mock
from deep_translator import GoogleTranslator, exceptions
from deep_translator.constants import GOOGLE_LANGUAGES_TO_CODES

@pytest.fixture
def google_translator():
    """Sample pytest fixture.
    See more at: http://doc.pytest.org/en/latest/fixture.html
    """
    return GoogleTranslator(target="en")

def test_content(google_translator):
    """Sample pytest test function with the pytest fixture as an argument."""
    assert google_translator.translate(text="좋은") == "good"

def test_abbreviations_and_languages_mapping():
    for abb, lang in GOOGLE_LANGUAGES_TO_CODES.items():
        g1 = GoogleTranslator(abb)
        g2 = GoogleTranslator(lang)
        assert g1._source == g2._source

def test_inputs():
    with pytest.raises(exceptions.InvalidSourceOrTargetLanguage):
        GoogleTranslator(source="", target="")

    with pytest.raises(exceptions.InvalidSourceOrTargetLanguage):
        GoogleTranslator(source="auto", target="")

    with pytest.raises(exceptions.InvalidSourceOrTargetLanguage):
        GoogleTranslator(source="", target="en")

def test_empty_text(google_translator):
    empty_txt = ""
    res = google_translator.translate(text=empty_txt)
    assert res == empty_txt

def test_payload(google_translator):
    with pytest.raises(exceptions.NotValidPayload):
        google_translator.translate(text={})

    with pytest.raises(exceptions.NotValidPayload):
        google_translator.translate(text=[])

    with pytest.raises(exceptions.NotValidLength):
        google_translator.translate("a" * 5001)

def test_one_character_words():
    assert (
        GoogleTranslator(source="es", target="en").translate("o") is not None
    )

@patch("deep_translator.google.GoogleTranslator._http_get")
def test_google_resilience_routing(mock_http_get):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = '<div class="t0">hello</div>'
    mock_http_get.return_value = mock_resp

    translator = GoogleTranslator(source="en", target="es")
    res = translator.translate("good")
    assert res == "hello"
    mock_http_get.assert_called_once()
    kwargs = mock_http_get.call_args[1]
    assert kwargs["check_response"] == translator._check_google_body

@patch("deep_translator.google.GoogleTranslator._http_get")
@patch("deep_translator.google.GoogleTranslator._http_get_once")
def test_google_fallback_uses_http_get_once(mock_http_get_once, mock_http_get):
    mock_resp1 = MagicMock()
    mock_resp1.status_code = 200
    mock_resp1.text = '<div class="t0">hello</div>'
    mock_http_get.return_value = mock_resp1

    mock_resp2 = MagicMock()
    mock_resp2.status_code = 200
    mock_resp2.text = '<div class="t0">hola</div>'
    mock_http_get_once.return_value = mock_resp2

    translator = GoogleTranslator(source="en", target="es")
    translator._url_params["hl"] = "en"
    
    with patch("time.time", return_value=1000.0):
        res = translator.translate("hello")
        assert res == "hola"
        mock_http_get.assert_called_once()
        mock_http_get_once.assert_called_once()
        assert "hl" not in translator._url_params

@patch("deep_translator.google.GoogleTranslator._http_get")
def test_google_exhaustion_maps_to_translation_not_found(mock_http_get):
    from deep_translator.net import TransientResponseError
    mock_http_get.side_effect = TransientResponseError("Exhausted", response=MagicMock())

    translator = GoogleTranslator(source="en", target="es")
    with pytest.raises(exceptions.TranslationNotFound):
        translator.translate("hello")

@patch("deep_translator.google.GoogleTranslator._http_get")
def test_google_http_error_mappings(mock_http_get):
    err_429 = urllib.error.HTTPError("http://google.com", 429, "Too Many Requests", None, None)
    mock_http_get.side_effect = err_429

    translator = GoogleTranslator(source="en", target="es")
    with pytest.raises(exceptions.TooManyRequests):
        translator.translate("hello")

    err_503 = urllib.error.HTTPError("http://google.com", 503, "Service Unavailable", None, None)
    mock_http_get.side_effect = err_503
    with pytest.raises(exceptions.RequestError):
        translator.translate("hello")

def test_google_backward_compatible_construction():
    t1 = GoogleTranslator(source="en", target="es")
    assert t1._timeout == 10
    
    t2 = GoogleTranslator(source="en", target="es", timeout=5, max_retries=2)
    assert t2._timeout == 5
    assert t2._max_retries == 2

def test_google_ahk_tokenization_round_trip():
    translator = GoogleTranslator(source="en", target="es")
    resp = MagicMock()
    resp.text = '<div class="t0">hello [[S]] backslash [[B]] newline [[N]]</div>'
    translator._check_google_body(resp)

    resp_trunc = MagicMock()
    resp_trunc.text = '<div class="t0">hello [[S]] backslash [[B]] newline [[N</div>'
    from deep_translator.net import TransientResponseError
    with pytest.raises(TransientResponseError):
        translator._check_google_body(resp_trunc)
