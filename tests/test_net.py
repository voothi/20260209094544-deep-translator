import pytest
import time
import json
import socket
import urllib.request
import urllib.error
import email
import gzip
import io
from unittest.mock import MagicMock, patch, Mock
from deep_translator.net import (
    DEFAULT_USER_AGENT,
    ResilientSession,
    TransientResponseError,
    is_transient,
    is_transient_status,
    is_transient_exception,
    parse_retry_after,
)

def make_mock_response(status, text, headers=None, url="http://test.com"):
    mock_resp = MagicMock()
    mock_resp.status = status
    mock_resp.url = url
    headers_bytes = b""
    if headers:
        headers_bytes = b"\r\n".join(f"{k}: {v}".encode("utf-8") for k, v in headers.items()) + b"\r\n"
    headers_bytes += b"\r\n"
    mock_resp.headers = email.message_from_bytes(headers_bytes)
    mock_resp.read.return_value = text.encode("utf-8")
    return mock_resp

def make_http_error(status, text, headers=None, url="http://test.com"):
    headers_bytes = b""
    if headers:
        headers_bytes = b"\r\n".join(f"{k}: {v}".encode("utf-8") for k, v in headers.items()) + b"\r\n"
    headers_bytes += b"\r\n"
    hdrs = email.message_from_bytes(headers_bytes)
    fp = io.BytesIO(text.encode("utf-8"))
    return urllib.error.HTTPError(url, status, "HTTP Error", hdrs, fp)

def test_transient_classifiers():
    assert is_transient_status(429) is True
    assert is_transient_status(500) is True
    assert is_transient_status(503) is True
    assert is_transient_status(200) is False
    assert is_transient_status(400) is False

    assert is_transient_exception(urllib.error.HTTPError("http://x.com", 429, "Err", None, None)) is True
    assert is_transient_exception(urllib.error.HTTPError("http://x.com", 500, "Err", None, None)) is True
    assert is_transient_exception(urllib.error.HTTPError("http://x.com", 400, "Err", None, None)) is False
    assert is_transient_exception(urllib.error.URLError("reason")) is True
    assert is_transient_exception(socket.timeout("timeout")) is True
    assert is_transient_exception(ConnectionError("conn")) is True
    assert is_transient_exception(TimeoutError("timeout")) is True
    assert is_transient_exception(json.JSONDecodeError("msg", "doc", 0)) is True
    assert is_transient_exception(TransientResponseError("err")) is True
    assert is_transient_exception(ValueError("Fatal")) is False

    assert is_transient(429) is True
    assert is_transient(socket.timeout("timeout")) is True

def test_parse_retry_after():
    assert parse_retry_after("15") == 15.0
    assert parse_retry_after("invalid") == 0.0
    future_date = "Wed, 21 Oct 2099 07:28:00 GMT"
    val = parse_retry_after(future_date)
    assert val > 0.0

@patch("urllib.request.build_opener")
def test_resilient_session_ua_and_proxies(mock_build_opener):
    mock_opener = MagicMock()
    mock_build_opener.return_value = mock_opener

    proxies = {"https": "http://127.0.0.1:8080"}
    with ResilientSession(proxies=proxies) as session:
        assert session._opener is None
        opener = session._get_opener()
        assert opener == mock_opener
        mock_build_opener.assert_called_once()
        args, kwargs = mock_build_opener.call_args
        # ProxyHandler should be in build_opener args
        handlers = args
        assert len(handlers) == 1
        assert isinstance(handlers[0], urllib.request.ProxyHandler)

@patch("urllib.request.build_opener")
def test_transient_then_success(mock_build_opener):
    mock_opener = MagicMock()
    mock_build_opener.return_value = mock_opener

    mock_resp = make_mock_response(200, "Success")
    mock_opener.open.side_effect = [
        urllib.error.URLError("Connection refused"),
        mock_resp
    ]

    retries_info = []
    def on_retry(attempt, delay, reason):
        retries_info.append((attempt, reason))

    session = ResilientSession()
    res = session.request(
        "GET", "http://test.com",
        max_retries=2, retry_backoff=0.01, retry_jitter=0.01,
        on_retry=on_retry
    )

    assert res.text == "Success"
    assert len(retries_info) == 1
    assert retries_info[0] == (1, "URLError")

@patch("urllib.request.build_opener")
def test_budget_exhaustion(mock_build_opener):
    mock_opener = MagicMock()
    mock_build_opener.return_value = mock_opener
    mock_opener.open.side_effect = socket.timeout("Timed out")

    session = ResilientSession()
    with pytest.raises(socket.timeout) as excinfo:
        session.request(
            "GET", "http://test.com",
            max_retries=2, retry_backoff=0.01, retry_jitter=0.01
        )

    assert "Request failed after 3 attempts" in str(excinfo.value)
    assert "Timed out" in str(excinfo.value)

@patch("urllib.request.build_opener")
def test_non_transient_error(mock_build_opener):
    mock_opener = MagicMock()
    mock_build_opener.return_value = mock_opener
    mock_opener.open.side_effect = ValueError("Fatal input error")

    session = ResilientSession()
    with pytest.raises(ValueError) as excinfo:
        session.request(
            "GET", "http://test.com",
            max_retries=2, retry_backoff=0.01, retry_jitter=0.01
        )

    assert "Fatal input error" in str(excinfo.value)
    assert mock_opener.open.call_count == 1

@patch("urllib.request.build_opener")
def test_endless_mode(mock_build_opener):
    mock_opener = MagicMock()
    mock_build_opener.return_value = mock_opener

    mock_resp = make_mock_response(200, "Endless success")
    fail_err = make_http_error(503, "Service Unavailable")

    mock_opener.open.side_effect = [
        fail_err, fail_err, fail_err, fail_err, fail_err,
        mock_resp
    ]

    session = ResilientSession()
    res = session.request(
        "GET", "http://test.com",
        max_retries=-1, retry_backoff=0.001, retry_jitter=0.001
    )
    assert res.text == "Endless success"
    assert mock_opener.open.call_count == 6

@patch("urllib.request.build_opener")
def test_max_total_time_deadline(mock_build_opener):
    mock_opener = MagicMock()
    mock_build_opener.return_value = mock_opener
    mock_opener.open.side_effect = socket.timeout("Timed out")

    session = ResilientSession()
    with pytest.raises(socket.timeout):
        session.request(
            "GET", "http://test.com",
            max_retries=5, retry_backoff=0.5, retry_jitter=0.1,
            max_total_time=0.05
        )

@patch("urllib.request.build_opener")
def test_check_response_callback(mock_build_opener):
    mock_opener = MagicMock()
    mock_build_opener.return_value = mock_opener

    mock_resp_empty = make_mock_response(200, "")
    mock_resp_ok = make_mock_response(200, "Hello World")
    mock_opener.open.side_effect = [mock_resp_empty, mock_resp_ok]

    def check_response(resp):
        if not resp.text:
            raise TransientResponseError("Empty response body", response=resp)

    session = ResilientSession()
    res = session.request(
        "GET", "http://test.com",
        max_retries=2, retry_backoff=0.01, retry_jitter=0.01,
        check_response=check_response
    )
    assert res.text == "Hello World"
    assert mock_opener.open.call_count == 2

@patch("urllib.request.build_opener")
def test_check_response_not_called_on_transient_status(mock_build_opener):
    mock_opener = MagicMock()
    mock_build_opener.return_value = mock_opener

    fail_err = make_http_error(429, "Too Many Requests")
    mock_resp_ok = make_mock_response(200, "OK")
    mock_opener.open.side_effect = [fail_err, mock_resp_ok]

    check_called = False
    def check_response(resp):
        nonlocal check_called
        check_called = True

    session = ResilientSession()
    session.request(
        "GET", "http://test.com",
        max_retries=2, retry_backoff=0.01, retry_jitter=0.01,
        check_response=check_response
    )
    assert check_called is True

    # Try again with max_retries=0 on transient status code, check_response should not fire
    mock_opener.open.side_effect = [fail_err]
    check_called = False
    with pytest.raises(urllib.error.HTTPError):
        session.request(
            "GET", "http://test.com",
            max_retries=0, check_response=check_response
        )
    assert check_called is False

@patch("urllib.request.build_opener")
def test_request_once_limit(mock_build_opener):
    mock_opener = MagicMock()
    mock_build_opener.return_value = mock_opener
    mock_opener.open.side_effect = socket.timeout("Timed out")

    session = ResilientSession()
    with pytest.raises(socket.timeout) as excinfo:
        session.request_once("GET", "http://test.com", retry_backoff=0.01, retry_jitter=0.01)
    
    assert "2 attempts" in str(excinfo.value)

@patch("urllib.request.build_opener")
def test_interruptible_sleep(mock_build_opener):
    mock_opener = MagicMock()
    mock_build_opener.return_value = mock_opener
    mock_opener.open.side_effect = socket.timeout("Timed out")

    session = ResilientSession()
    sleep_calls = 0
    def mock_sleep(sec):
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls >= 1:
            raise KeyboardInterrupt()

    with patch("time.sleep", side_effect=mock_sleep):
        with pytest.raises(KeyboardInterrupt):
            session.request(
                "GET", "http://test.com",
                max_retries=2, retry_backoff=1.0, retry_jitter=0.1
            )

@patch("urllib.request.build_opener")
def test_dict_payload_serialization(mock_build_opener):
    mock_opener = MagicMock()
    mock_build_opener.return_value = mock_opener
    mock_resp = make_mock_response(200, "OK")
    mock_opener.open.return_value = mock_resp

    session = ResilientSession()
    data = {"text": "hello", "target": "es"}
    session.request("POST", "http://test.com", data=data)

    mock_opener.open.assert_called_once()
    req = mock_opener.open.call_args[0][0]
    assert req.method == "POST"
    assert req.data == b"text=hello&target=es"
    assert req.headers["Content-type"] == "application/x-www-form-urlencoded"

@patch("urllib.request.build_opener")
def test_defensive_gzip(mock_build_opener):
    mock_opener = MagicMock()
    mock_build_opener.return_value = mock_opener

    plain_text = "decompressed text"
    compressed = gzip.compress(plain_text.encode("utf-8"))
    mock_resp = make_mock_response(200, "", headers={"Content-Encoding": "gzip"})
    mock_resp.read.return_value = compressed
    mock_opener.open.return_value = mock_resp

    session = ResilientSession()
    res = session.request("GET", "http://test.com")
    assert res.text == plain_text

@patch("urllib.request.build_opener")
def test_case_insensitive_headers(mock_build_opener):
    mock_opener = MagicMock()
    mock_build_opener.return_value = mock_opener
    mock_resp = make_mock_response(200, "OK", headers={"Retry-After": "45"})
    mock_opener.open.return_value = mock_resp

    session = ResilientSession()
    res = session.request("GET", "http://test.com")
    assert res.headers.get("retry-after") == "45"
    assert res.headers.get("Retry-After") == "45"

@patch("urllib.request.build_opener")
def test_d3a_d3b_split(mock_build_opener):
    mock_opener = MagicMock()
    mock_build_opener.return_value = mock_opener

    # Transient 429 error raised during request
    err_429 = make_http_error(429, "Too many requests")
    mock_opener.open.side_effect = err_429

    session = ResilientSession()
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        session.request("GET", "http://test.com", max_retries=0)
    assert excinfo.value.code == 429

    # Non-transient 403 error: ResponseShim should be returned, not raised
    err_403 = make_http_error(403, "Forbidden")
    mock_opener.open.side_effect = None
    mock_opener.open.return_value = err_403
    # Wait, in urllib, opener.open actually raises HTTPError.
    # But in ResilientSession.request, it catches HTTPError, wraps in ResponseShim, and returns it.
    mock_opener.open.side_effect = err_403
    res = session.request("GET", "http://test.com", max_retries=0)
    assert res.status_code == 403


def test_resilient_session_initializes_with_browser_user_agent():
    session = ResilientSession()
    assert "User-Agent" in session._headers
    assert session._headers["User-Agent"] == DEFAULT_USER_AGENT


def test_resilient_session_requests_session_carries_browser_user_agent():
    mock_req_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = "OK"
    mock_resp.headers = {}
    mock_req_session.request.return_value = mock_resp

    with ResilientSession(session=mock_req_session) as r_session:
        res = r_session.request("GET", "http://test.com/api")
        assert res.text == "OK"
        mock_req_session.request.assert_called_once()
        _, kwargs = mock_req_session.request.call_args
        assert "headers" in kwargs
        assert kwargs["headers"].get("User-Agent") == DEFAULT_USER_AGENT


def test_resilient_session_requests_session_merges_and_overrides_headers():
    mock_req_session = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = "OK"
    mock_resp.headers = {}
    mock_req_session.request.return_value = mock_resp

    with ResilientSession(session=mock_req_session) as r_session:
        # Merge additional headers while preserving default User-Agent
        r_session.request("GET", "http://test.com/api", headers={"Authorization": "Bearer token"})
        _, kwargs = mock_req_session.request.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer token"
        assert kwargs["headers"]["User-Agent"] == DEFAULT_USER_AGENT

        # Explicitly overriding User-Agent
        custom_ua = "Custom-Agent/1.0"
        r_session.request("GET", "http://test.com/api", headers={"User-Agent": custom_ua})
        _, kwargs = mock_req_session.request.call_args
        assert kwargs["headers"]["User-Agent"] == custom_ua

