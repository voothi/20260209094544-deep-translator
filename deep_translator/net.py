import time
import random
from typing import Callable, Optional, Union
from deep_translator._httpresponse import ResponseShim

# Default constants
DEFAULT_TIMEOUT = 10
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_BASE = 1.0
DEFAULT_BACKOFF_JITTER = 1.0
DEFAULT_MAX_DELAY = 30.0

class TransientResponseError(Exception):
    """
    Exception raised when a 2xx response has an empty, truncated, or unparseable body.
    """
    def __init__(self, message: str, response=None, text: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.response = response
        self.text = text or (response.text if response is not None else None)

def is_transient_status(status: int) -> bool:
    """Classify transient HTTP status codes."""
    return status == 429 or (500 <= status <= 504)

def is_transient_exception(exc: BaseException) -> bool:
    """Classify transient network/transport exceptions."""
    import json
    import urllib.error
    import socket
    
    if isinstance(exc, urllib.error.HTTPError):
        return is_transient_status(exc.code)
        
    if isinstance(exc, (
        urllib.error.URLError,
        socket.timeout,
        ConnectionError,
        TimeoutError,
        json.JSONDecodeError,
        TransientResponseError
    )):
        return True
    return False

def is_transient(value: Union[int, BaseException]) -> bool:
    """Convenience dispatcher for transient classification in tests/external code."""
    if isinstance(value, int):
        return is_transient_status(value)
    elif isinstance(value, BaseException):
        return is_transient_exception(value)
    return False

def parse_retry_after(header_val: str) -> float:
    """Parse Retry-After header which can be either seconds or an HTTP-date string."""
    try:
        return float(header_val)
    except ValueError:
        pass
    try:
        import email.utils
        from datetime import datetime, timezone
        dt = email.utils.parsedate_to_datetime(header_val)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        diff = (dt - now).total_seconds()
        return max(0.0, diff)
    except Exception:
        return 0.0

def raise_exhausted_error(last_error: BaseException, attempt: int):
    """Raise the last error preserved as its original class with a descriptive message."""
    import urllib.error
    import json
    
    status_code_str = ""
    if hasattr(last_error, "code"):
        status_code_str = f" (status code: {last_error.code})"
    elif hasattr(last_error, "response") and last_error.response is not None:
        status_code_str = f" (status code: {last_error.response.status_code})"
    
    new_message = f"Request failed after {attempt} attempts{status_code_str}. Original error: {str(last_error)}"
    exc_class = last_error.__class__
    
    try:
        if exc_class == TransientResponseError:
            new_exc = TransientResponseError(
                new_message,
                response=getattr(last_error, "response", None),
                text=getattr(last_error, "text", None)
            )
        elif exc_class == urllib.error.HTTPError:
            new_exc = urllib.error.HTTPError(
                getattr(last_error, "filename", getattr(last_error, "url", "")),
                last_error.code,
                new_message,
                last_error.hdrs,
                last_error.fp
            )
            for attr in ("response", "request"):
                if hasattr(last_error, attr):
                    setattr(new_exc, attr, getattr(last_error, attr))
        elif isinstance(last_error, json.JSONDecodeError):
            try:
                new_exc = exc_class(new_message, last_error.doc, last_error.pos)
            except Exception:
                new_exc = exc_class(new_message)
            for attr in ("response", "request", "doc", "pos", "lineno", "colno"):
                if hasattr(last_error, attr):
                    setattr(new_exc, attr, getattr(last_error, attr))
        elif exc_class == urllib.error.URLError:
            new_exc = urllib.error.URLError(new_message)
            if hasattr(last_error, "reason"):
                new_exc.reason = last_error.reason
            for attr in ("response", "request"):
                if hasattr(last_error, attr):
                    setattr(new_exc, attr, getattr(last_error, attr))
        else:
            new_exc = exc_class(new_message)
            for attr in ("response", "request"):
                if hasattr(last_error, attr):
                    setattr(new_exc, attr, getattr(last_error, attr))
    except Exception:
        new_exc = last_error
        
    raise new_exc

class ResilientSession:
    """urllib.request-backed session wrapper with built-in retry and timeout resilience."""
    def __init__(self, proxies: Optional[dict] = None):
        self.proxies = proxies
        self._opener = None
        self._ua_set = False
        self._headers = {}

    def _get_opener(self):
        if self._opener is None:
            import urllib.request
            handlers = []
            if self.proxies:
                handlers.append(urllib.request.ProxyHandler(self.proxies))
            self._opener = urllib.request.build_opener(*handlers)
        
        if not self._ua_set:
            self._headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            self._ua_set = True
        return self._opener

    def close(self):
        self._opener = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def request(
        self,
        method: str,
        url: str,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_backoff: float = DEFAULT_BACKOFF_BASE,
        retry_jitter: float = DEFAULT_BACKOFF_JITTER,
        max_delay: float = DEFAULT_MAX_DELAY,
        max_total_time: Optional[float] = None,
        check_response: Optional[Callable] = None,
        on_retry: Optional[Callable] = None,
        **kwargs
    ):
        import urllib.request
        import urllib.error
        import urllib.parse
        import socket
        import time
        import random

        data = kwargs.pop("data", None)
        headers = kwargs.pop("headers", None)
        params = kwargs.pop("params", None)

        if params:
            url_parts = list(urllib.parse.urlparse(url))
            query = dict(urllib.parse.parse_qsl(url_parts[4]))
            query.update(params)
            url_parts[4] = urllib.parse.urlencode(query)
            url = urllib.parse.urlunparse(url_parts)

        # Dictionary-payload auto-serialization
        if isinstance(data, dict):
            data = urllib.parse.urlencode(data).encode("utf-8")
            has_content_type = False
            if headers:
                for k in headers:
                    if k.lower() == "content-type":
                        has_content_type = True
                        break
            if not has_content_type:
                headers = headers or {}
                headers["Content-Type"] = "application/x-www-form-urlencoded"

        # Build headers to send
        headers_to_send = self._headers.copy()
        if headers:
            for k, v in headers.items():
                for existing_key in list(headers_to_send.keys()):
                    if existing_key.lower() == k.lower():
                        del headers_to_send[existing_key]
                headers_to_send[k] = v

        start_time = time.time()
        attempt = 0
        last_error = None

        while True:
            # Check deadline before attempt
            elapsed = time.time() - start_time
            if max_total_time is not None and elapsed >= max_total_time:
                if last_error is not None:
                    raise_exhausted_error(last_error, attempt)
                else:
                    raise TimeoutError(f"Request deadline of {max_total_time}s exceeded before attempt.")

            attempt += 1
            response = None
            current_error = None

            # Re-build Request object per attempt to avoid stale internal state
            req = urllib.request.Request(url, data=data, headers=headers_to_send, method=method)

            try:
                opener = self._get_opener()
                raw_response = opener.open(req, timeout=timeout)
                response = ResponseShim(raw_response)
            except urllib.error.HTTPError as e:
                response = ResponseShim(e)
                if is_transient_status(e.code):
                    e.response = response
                    current_error = e
                else:
                    # Non-transient HTTPError (e.g. 400, 403, 404).
                    # Run check_response callback on ResponseShim
                    if check_response is not None:
                        try:
                            check_response(response)
                        except Exception as val_err:
                            current_error = val_err
                    # If verification passes, return the response shim
                    if current_error is None:
                        return response
            except Exception as e:
                current_error = e

            # If success (2xx) and no error, run custom validator
            if current_error is None and response is not None:
                if check_response is not None:
                    try:
                        check_response(response)
                    except Exception as e:
                        current_error = e

            if current_error is not None:
                # Classify transient vs fatal exception
                if not is_transient_exception(current_error):
                    raise current_error
                last_error = current_error
            else:
                # Successful response
                return response

            # Check if retry budget is exhausted
            if max_retries is not None and max_retries != -1:
                if (attempt - 1) >= max_retries:
                    raise_exhausted_error(last_error, attempt)

            # Compute backoff with jitter
            delay = retry_backoff * (2 ** attempt) + random.uniform(0, retry_jitter)
            delay = min(delay, max_delay)

            # Honor Retry-After if available
            if response is not None and response.headers:
                retry_after_val = response.headers.get("Retry-After")
                if retry_after_val is not None:
                    retry_after = parse_retry_after(retry_after_val)
                    delay = max(delay, retry_after)
                    delay = min(delay, max_delay)

            # Check if next sleep would exceed overall deadline
            elapsed = time.time() - start_time
            if max_total_time is not None:
                if elapsed >= max_total_time or (elapsed + delay) > max_total_time:
                    raise_exhausted_error(last_error, attempt)

            # Dispatch observability callback
            if on_retry is not None:
                if isinstance(last_error, urllib.error.HTTPError):
                    reason = f"HTTP {last_error.code}"
                else:
                    reason = last_error.__class__.__name__
                on_retry(attempt, delay, reason)

            # Sleep in small slices to remain interruptible
            slice_duration = 0.2
            slept = 0.0
            while slept < delay:
                to_sleep = min(slice_duration, delay - slept)
                time.sleep(to_sleep)
                slept += to_sleep

    def request_once(self, method: str, url: str, **kwargs):
        """Perform a request with a small bounded retry limit (default max_retries=1)."""
        kwargs.setdefault("max_retries", 1)
        return self.request(method, url, **kwargs)

    def get(self, url: str, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs):
        return self.request("POST", url, **kwargs)

def request_get(url: str, **kwargs):
    with ResilientSession() as session:
        return session.get(url, **kwargs)

def request_post(url: str, **kwargs):
    with ResilientSession() as session:
        return session.post(url, **kwargs)
