import pytest
import time
import sys
from unittest.mock import MagicMock, patch
from deep_translator import GoogleTranslator
from deep_translator.net import ResilientSession

def test_google_no_request_path_lazy_session():
    # Constructing and calling with same source/target should not initialize resilient session
    translator = GoogleTranslator(source="en", target="en")
    assert translator._resilient_session is None
    res = translator.translate("hello")
    assert res == "hello"
    assert translator._resilient_session is None

@patch("urllib.request.build_opener")
def test_healthy_first_attempt_no_overhead(mock_build_opener):
    mock_opener = MagicMock()
    mock_build_opener.return_value = mock_opener
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = b"OK"
    mock_opener.open.return_value = mock_resp

    session = ResilientSession()
    on_retry_called = False
    def on_retry(a, d, r):
        nonlocal on_retry_called
        on_retry_called = True

    with patch("time.sleep") as mock_sleep:
        session.request("GET", "http://test.com", on_retry=on_retry)
        mock_sleep.assert_not_called()
        assert on_retry_called is False

def test_no_heavy_imports():
    assert "httpx" not in sys.modules
    assert "aiohttp" not in sys.modules
    assert "tenacity" not in sys.modules

def test_google_check_body_perf():
    translator = GoogleTranslator(source="en", target="es")
    resp = MagicMock()
    resp.text = '<div class="t0">translated text</div>'
    
    start = time.perf_counter()
    for _ in range(100):
        translator._check_google_body(resp)
    end = time.perf_counter()
    avg_time_ms = ((end - start) / 100) * 1000
    assert avg_time_ms < 5.0  # must be under 5ms on average

@patch("urllib.request.build_opener")
def test_response_json_caching(mock_build_opener):
    mock_opener = MagicMock()
    mock_build_opener.return_value = mock_opener
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.headers = None
    mock_resp.read.return_value = b'{"hello": "world"}'
    mock_opener.open.return_value = mock_resp

    session = ResilientSession()
    res = session.request("GET", "http://test.com")
    
    j1 = res.json()
    j2 = res.json()
    assert j1 is j2  # ResilientSession wraps and caches the dict object
