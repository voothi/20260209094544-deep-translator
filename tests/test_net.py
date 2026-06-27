import pytest
import time
import json
import requests
from unittest.mock import MagicMock, patch, Mock
from deep_translator.net import (
    ResilientSession,
    TransientResponseError,
    is_transient,
    is_transient_status,
    is_transient_exception,
    parse_retry_after,
)

def test_transient_classifiers():
    # Statuses
    assert is_transient_status(429) is True
    assert is_transient_status(500) is True
    assert is_transient_status(503) is True
    assert is_transient_status(200) is False
    assert is_transient_status(400) is False

    # Exceptions
    assert is_transient_exception(requests.exceptions.Timeout("Timeout")) is True
    assert is_transient_exception(requests.exceptions.ConnectionError("Conn")) is True
    assert is_transient_exception(requests.exceptions.ProxyError("Proxy")) is True
    assert is_transient_exception(requests.exceptions.ChunkedEncodingError("Chunk")) is True
    assert is_transient_exception(json.JSONDecodeError("msg", "doc", 0)) is True
    assert is_transient_exception(TransientResponseError("err")) is True
    assert is_transient_exception(ValueError("Fatal")) is False

    # Dispatcher
    assert is_transient(429) is True
    assert is_transient(requests.exceptions.Timeout("T")) is True

def test_parse_retry_after():
    assert parse_retry_after("15") == 15.0
    assert parse_retry_after("invalid") == 0.0
    # HTTP-date
    future_date = "Wed, 21 Oct 2099 07:28:00 GMT"
    val = parse_retry_after(future_date)
    assert val > 0.0

@patch("requests.Session")
def test_resilient_session_ua_and_proxies(mock_session_cls):
    mock_sess = MagicMock()
    mock_session_cls.return_value = mock_sess
    mock_sess.headers = {}

    proxies = {"https": "http://127.0.0.1:8080"}
    with ResilientSession(proxies=proxies) as session:
        # Check lazy creation
        assert session._session is None
        sess = session._get_session()
        assert sess == mock_sess
        assert mock_sess.proxies == proxies
        # UA set
        assert "User-Agent" in mock_sess.headers
        assert "Chrome" in mock_sess.headers["User-Agent"]

@patch("requests.Session")
def test_transient_then_success(mock_session_cls):
    mock_sess = MagicMock()
    mock_session_cls.return_value = mock_sess

    # First attempt raises ConnectionError, second succeeds
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "Success"

    mock_sess.request.side_effect = [
        requests.exceptions.ConnectionError("Transient connection issue"),
        mock_response
    ]

    retries_info = []
    def on_retry(attempt, delay, reason):
        retries_info.append((attempt, reason))

    session = ResilientSession()
    # Use small base/jitter so test runs instantly
    res = session.request(
        "GET", "http://test.com",
        max_retries=2, retry_backoff=0.01, retry_jitter=0.01,
        on_retry=on_retry
    )

    assert res.text == "Success"
    assert len(retries_info) == 1
    assert retries_info[0] == (1, "ConnectionError")

@patch("requests.Session")
def test_budget_exhaustion(mock_session_cls):
    mock_sess = MagicMock()
    mock_session_cls.return_value = mock_sess
    mock_sess.request.side_effect = requests.exceptions.Timeout("Timeout occurred")

    session = ResilientSession()
    with pytest.raises(requests.exceptions.Timeout) as excinfo:
        session.request(
            "GET", "http://test.com",
            max_retries=2, retry_backoff=0.01, retry_jitter=0.01
        )

    # Message must indicate attempt count and last error
    assert "Request failed after 3 attempts" in str(excinfo.value)
    assert "Timeout occurred" in str(excinfo.value)

@patch("requests.Session")
def test_non_transient_error(mock_session_cls):
    mock_sess = MagicMock()
    mock_session_cls.return_value = mock_sess
    mock_sess.request.side_effect = ValueError("Fatal input error")

    session = ResilientSession()
    # Should raise immediately without retry
    with pytest.raises(ValueError) as excinfo:
        session.request(
            "GET", "http://test.com",
            max_retries=2, retry_backoff=0.01, retry_jitter=0.01
        )

    assert "Fatal input error" in str(excinfo.value)
    # Check that request was called only once
    assert mock_sess.request.call_count == 1

@patch("requests.Session")
def test_endless_mode(mock_session_cls):
    mock_sess = MagicMock()
    mock_session_cls.return_value = mock_sess

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = "Endless success"

    # Fail 5 times with HTTP 503, then succeed
    fail_resp = MagicMock()
    fail_resp.status_code = 503

    mock_sess.request.side_effect = [
        requests.exceptions.HTTPError("Service Unavailable", response=fail_resp),
        requests.exceptions.HTTPError("Service Unavailable", response=fail_resp),
        requests.exceptions.HTTPError("Service Unavailable", response=fail_resp),
        requests.exceptions.HTTPError("Service Unavailable", response=fail_resp),
        requests.exceptions.HTTPError("Service Unavailable", response=fail_resp),
        mock_resp
    ]

    session = ResilientSession()
    res = session.request(
        "GET", "http://test.com",
        max_retries=-1, retry_backoff=0.001, retry_jitter=0.001
    )
    assert res.text == "Endless success"
    assert mock_sess.request.call_count == 6

@patch("requests.Session")
def test_max_total_time_deadline(mock_session_cls):
    mock_sess = MagicMock()
    mock_session_cls.return_value = mock_sess
    mock_sess.request.side_effect = requests.exceptions.Timeout("Timeout")

    session = ResilientSession()
    # 0.1s max deadline, delay would be larger, so it halts
    with pytest.raises(requests.exceptions.Timeout):
        session.request(
            "GET", "http://test.com",
            max_retries=5, retry_backoff=0.5, retry_jitter=0.1,
            max_total_time=0.05
        )

@patch("requests.Session")
def test_check_response_callback(mock_session_cls):
    mock_sess = MagicMock()
    mock_session_cls.return_value = mock_sess

    # Mock response returns empty body
    mock_resp_empty = MagicMock()
    mock_resp_empty.status_code = 200
    mock_resp_empty.text = ""

    mock_resp_ok = MagicMock()
    mock_resp_ok.status_code = 200
    mock_resp_ok.text = "Hello World"

    mock_sess.request.side_effect = [mock_resp_empty, mock_resp_ok]

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
    assert mock_sess.request.call_count == 2

@patch("requests.Session")
def test_check_response_not_called_on_transient_status(mock_session_cls):
    mock_sess = MagicMock()
    mock_session_cls.return_value = mock_sess

    fail_resp = MagicMock()
    fail_resp.status_code = 429

    mock_resp_ok = MagicMock()
    mock_resp_ok.status_code = 200
    mock_resp_ok.text = "OK"

    mock_sess.request.side_effect = [fail_resp, mock_resp_ok]

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
    # check_response should not be called on the 429 response, only on 200
    assert check_called is True
    # If check_response was called on 429, we would know, but here we can check call arguments
    # Let's verify check_response wasn't called with fail_resp
    # Since check_called is True, it must have been called for mock_resp_ok.
    mock_sess.request.side_effect = [fail_resp]
    check_called = False
    with pytest.raises(requests.exceptions.HTTPError):
        session.request(
            "GET", "http://test.com",
            max_retries=0, check_response=check_response
        )
    assert check_called is False

@patch("requests.Session")
def test_request_once_limit(mock_session_cls):
    mock_sess = MagicMock()
    mock_session_cls.return_value = mock_sess
    mock_sess.request.side_effect = requests.exceptions.Timeout("Timeout")

    session = ResilientSession()
    # request_once defaults to max_retries=1 (2 total attempts)
    with pytest.raises(requests.exceptions.Timeout) as excinfo:
        session.request_once("GET", "http://test.com", retry_backoff=0.01, retry_jitter=0.01)
    
    assert "2 attempts" in str(excinfo.value)

@patch("requests.Session")
def test_interruptible_sleep(mock_session_cls):
    mock_sess = MagicMock()
    mock_session_cls.return_value = mock_sess
    mock_sess.request.side_effect = requests.exceptions.Timeout("Timeout")

    session = ResilientSession()
    
    # We mock time.sleep to raise KeyboardInterrupt on the second call (inside sleep loop)
    # wait, we can mock time.sleep
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
