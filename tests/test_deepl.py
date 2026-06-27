from unittest.mock import Mock, patch, MagicMock
import pytest
import json
import urllib.error
from deep_translator.deepl import DeeplTranslator
from deep_translator.exceptions import AuthorizationException, ServerException, TranslationNotFound

@patch("deep_translator.deepl.DeeplTranslator._http_post")
def test_simple_translation(mock_http_post):
    translator = DeeplTranslator(
        api_key="imagine-this-is-an-valid-api-key", source="en", target="es"
    )
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"translations": [{"text": "hola"}]}
    mock_http_post.return_value = mock_response
    translation = translator.translate("hello")
    assert translation == "hola"
    mock_http_post.assert_called_once()
    kwargs = mock_http_post.call_args[1]
    assert kwargs["check_response"] == translator._check_deepl_body

@patch("deep_translator.deepl.DeeplTranslator._http_post")
def test_wrong_api_key(mock_http_post):
    translator = DeeplTranslator(
        api_key="this-is-a-wrong-api-key!", source="en", target="es"
    )
    resp = MagicMock()
    resp.status_code = 403
    mock_http_post.return_value = resp
    with pytest.raises(AuthorizationException):
        translator.translate("Hello")

@patch("deep_translator.deepl.DeeplTranslator._http_post")
def test_deepl_connection_error_mapping(mock_http_post):
    translator = DeeplTranslator(
        api_key="key", source="en", target="es"
    )
    mock_http_post.side_effect = ConnectionError("Connection failed")
    with pytest.raises(ServerException) as excinfo:
        translator.translate("Hello")
    assert "ERR_SERVICE_NOT_AVAIBLE" in str(excinfo.value)  # status 503 is mapped to ERR_SERVICE_NOT_AVAIBLE

@patch("deep_translator.deepl.DeeplTranslator._http_post")
def test_deepl_auth_header_sent(mock_http_post):
    translator = DeeplTranslator(
        api_key="my-key-123", source="en", target="es"
    )
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"translations": [{"text": "hola"}]}
    mock_http_post.return_value = mock_response

    translator.translate("hello")
    mock_http_post.assert_called_once()
    kwargs = mock_http_post.call_args[1]
    assert "headers" in kwargs
    assert kwargs["headers"]["Authorization"] == "DeepL-Auth-Key my-key-123"

def test_deepl_ahk_tokenization_round_trip():
    translator = DeeplTranslator(api_key="key", source="en", target="es")
    resp = MagicMock()
    resp.text = '{"translations": [{"text": "hello [[S]] backslash [[B]] newline [[N]]"}]}'
    resp.json.return_value = json.loads(resp.text)
    # Should not raise error
    translator._check_deepl_body(resp)

    resp_trunc = MagicMock()
    resp_trunc.text = '{"translations": [{"text": "hello [[S]] backslash [[B]] newline [[N"'
    from deep_translator.net import TransientResponseError
    with pytest.raises(TransientResponseError):
        translator._check_deepl_body(resp_trunc)
