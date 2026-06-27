import time
import random
import datetime
from typing import Callable, Optional, Union

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
    try:
        import requests
        requests_exceptions = (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
            requests.exceptions.ProxyError,
            requests.exceptions.ChunkedEncodingError,
        )
        if isinstance(exc, requests.exceptions.HTTPError) and exc.response is not None:
            if is_transient_status(exc.response.status_code):
                return True
        requests_json_err = getattr(requests.exceptions, "JSONDecodeError", None)
        if requests_json_err and isinstance(exc, requests_json_err):
            return True
    except ImportError:
        requests_exceptions = ()

    if isinstance(exc, requests_exceptions):
        return True
    if isinstance(exc, (ConnectionError, json.JSONDecodeError, TransientResponseError)):
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
    status_code_str = ""
    if hasattr(last_error, "response") and last_error.response is not None:
        status_code_str = f" (status code: {last_error.response.status_code})"
    
    new_message = f"Request failed after {attempt} attempts{status_code_str}. Original error: {str(last_error)}"
    exc_class = last_error.__class__
    
    try:
        import json
        if exc_class == TransientResponseError:
            new_exc = TransientResponseError(
                new_message,
                response=getattr(last_error, "response", None),
                text=getattr(last_error, "text", None)
            )
        elif isinstance(last_error, json.JSONDecodeError):
            try:
                new_exc = exc_class(new_message, last_error.doc, last_error.pos)
            except Exception:
                new_exc = exc_class(new_message)
            for attr in ("response", "request", "doc", "pos", "lineno", "colno"):
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
    """Reusable requests.Session wrapper with built-in retry and timeout resilience."""
    def __init__(self, proxies: Optional[dict] = None):
        self.proxies = proxies
        self._session = None
        self._ua_set = False

    def _get_session(self):
        if self._session is None:
            import requests
            self._session = requests.Session()
            if self.proxies:
                self._session.proxies = self.proxies
        if not self._ua_set:
            self._session.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            })
            self._ua_set = True
        return self._session

    def close(self):
        if self._session is not None:
            self._session.close()
            self._session = None

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
        import requests
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

            try:
                session = self._get_session()
                response = session.request(method, url, timeout=timeout, **kwargs)
            except Exception as e:
                current_error = e

            # If no exception, check transient status codes and run custom validator
            if current_error is None and response is not None:
                # Cache response.json() calls
                _orig_json = response.json
                _cached_json = None
                def cached_json(**json_kwargs):
                    nonlocal _cached_json
                    if _cached_json is None or json_kwargs:
                        res_val = _orig_json(**json_kwargs)
                        if not json_kwargs:
                            _cached_json = res_val
                        return res_val
                    return _cached_json
                response.json = cached_json

                if is_transient_status(response.status_code):
                    current_error = requests.exceptions.HTTPError(
                        f"HTTP {response.status_code}", response=response
                    )
                else:
                    # check_response callback contract
                    if check_response is not None:
                        try:
                            check_response(response)
                        except Exception as e:
                            current_error = e

            if current_error is not None:
                # Classify transient vs fatal exception
                if not is_transient_exception(current_error):
                    # Fatal error: raise immediately
                    raise current_error
                last_error = current_error
            else:
                # Successful response
                return response

            # Check if retry budget is exhausted
            # 0 retries means exactly 1 attempt
            if max_retries is not None and max_retries != -1:
                if (attempt - 1) >= max_retries:
                    raise_exhausted_error(last_error, attempt)

            # Compute backoff with jitter
            delay = retry_backoff * (2 ** attempt) + random.uniform(0, retry_jitter)
            delay = min(delay, max_delay)

            # Honor Retry-After if available
            if response is not None and "Retry-After" in response.headers:
                retry_after = parse_retry_after(response.headers["Retry-After"])
                delay = max(delay, retry_after)
                delay = min(delay, max_delay)

            # Check if next sleep would exceed overall deadline
            elapsed = time.time() - start_time
            if max_total_time is not None:
                if elapsed >= max_total_time or (elapsed + delay) > max_total_time:
                    raise_exhausted_error(last_error, attempt)

            # Dispatch observability callback
            if on_retry is not None:
                if isinstance(last_error, requests.exceptions.HTTPError) and last_error.response is not None:
                    reason = f"HTTP {last_error.response.status_code}"
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
