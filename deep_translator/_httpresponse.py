import gzip
import json
from typing import Any

class ResponseShim:
    def __init__(self, response: Any):
        self.url = getattr(response, "url", None)
        
        # Determine status/status_code
        self.status_code = getattr(response, "status", getattr(response, "code", None))
        self.status = self.status_code
        
        # Headers: exposed as the native case-insensitive http.client.HTTPMessage
        self.headers = getattr(response, "headers", None)
        
        # Read the raw body bytes exactly once in __init__
        try:
            raw_bytes = response.read()
        except Exception:
            raw_bytes = b""
        
        # Defensive gzip decompression
        if raw_bytes and self.headers:
            content_encoding = self.headers.get("Content-Encoding", "").lower()
            if "gzip" in content_encoding:
                try:
                    raw_bytes = gzip.decompress(raw_bytes)
                except Exception:
                    pass
        
        self._raw_bytes = raw_bytes
        self._text = None
        self._json_cache = None

    @property
    def text(self) -> str:
        if self._text is None:
            if not self._raw_bytes:
                self._text = ""
            else:
                self._text = self._raw_bytes.decode("utf-8", errors="replace")
        return self._text

    def json(self, **kwargs) -> Any:
        if self._json_cache is None or kwargs:
            res_val = json.loads(self.text, **kwargs)
            if not kwargs:
                self._json_cache = res_val
            return res_val
        return self._json_cache
