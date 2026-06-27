import io
import gzip
import json
import urllib.error
import email
import pytest
from unittest.mock import Mock

from deep_translator._httpresponse import ResponseShim

def test_response_shim_2xx():
    hdrs = email.message_from_bytes(b"Content-Type: application/json\r\nRetry-After: 30\r\n\r\n")
    mock_resp = Mock()
    mock_resp.status = 200
    mock_resp.headers = hdrs
    mock_resp.url = "https://example.com/api"
    mock_resp.read.return_value = b'{"translatedText": "hello"}'

    shim = ResponseShim(mock_resp)

    assert shim.status_code == 200
    assert shim.status == 200
    assert shim.url == "https://example.com/api"
    assert shim.headers == hdrs
    assert shim.headers["Retry-After"] == "30"
    assert shim.headers["retry-after"] == "30"
    assert shim.headers["RETRY-AFTER"] == "30"
    
    assert shim.text == '{"translatedText": "hello"}'
    
    assert shim.json() == {"translatedText": "hello"}
    assert shim.json() is shim.json()  # Cached (same object)
    
    assert mock_resp.read.call_count == 1

def test_response_shim_http_error():
    hdrs = email.message_from_bytes(b"Retry-After: 120\r\n\r\n")
    fp = io.BytesIO(b"Too many requests body")
    err = urllib.error.HTTPError("https://example.com/api", 429, "Too Many Requests", hdrs, fp)

    shim = ResponseShim(err)
    
    assert shim.status_code == 429
    assert shim.status == 429
    assert shim.url == "https://example.com/api"
    assert shim.headers == hdrs
    assert shim.headers["Retry-After"] == "120"
    assert shim.headers["retry-after"] == "120"
    assert shim.text == "Too many requests body"

def test_response_shim_empty_body():
    mock_resp = Mock()
    mock_resp.status = 204
    mock_resp.headers = email.message_from_bytes(b"\r\n")
    mock_resp.url = "https://example.com/empty"
    mock_resp.read.return_value = b""

    shim = ResponseShim(mock_resp)
    
    assert shim.text == ""
    with pytest.raises(json.JSONDecodeError):
        shim.json()

def test_response_shim_gzip_decompression():
    plain_text = '{"status": "ok"}'
    compressed = gzip.compress(plain_text.encode("utf-8"))
    
    hdrs = email.message_from_bytes(b"Content-Encoding: gzip\r\n\r\n")
    mock_resp = Mock()
    mock_resp.status = 200
    mock_resp.headers = hdrs
    mock_resp.url = "https://example.com/gzip"
    mock_resp.read.return_value = compressed
    
    shim = ResponseShim(mock_resp)
    assert shim.text == plain_text
    assert shim.json() == {"status": "ok"}

def test_response_shim_non_gzip_passthrough():
    plain_text = '{"status": "ok"}'
    
    hdrs = email.message_from_bytes(b"Content-Encoding: identity\r\n\r\n")
    mock_resp = Mock()
    mock_resp.status = 200
    mock_resp.headers = hdrs
    mock_resp.url = "https://example.com/plain"
    mock_resp.read.return_value = plain_text.encode("utf-8")
    
    shim = ResponseShim(mock_resp)
    assert shim.text == plain_text
