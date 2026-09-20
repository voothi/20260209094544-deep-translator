"""
google translator API
"""

__copyright__ = "Copyright (C) 2020 Nidhal Baccouri"

from typing import Any, List, Optional
import json

from bs4 import BeautifulSoup

from deep_translator.base import BaseTranslator
from deep_translator.constants import BASE_URLS
from deep_translator.exceptions import (
    RequestError,
    TooManyRequests,
    TranslationNotFound,
)
from deep_translator.validate import is_empty, is_input_valid, request_failed


class GoogleTranslator(BaseTranslator):
    """
    class that wraps functions, which use Google Translate under the hood to translate text(s)
    """

    def __init__(
        self,
        source: str = "auto",
        target: str = "en",
        proxies: Optional[dict] = None,
        session: Optional[object] = None,
        client: str = "dict-chrome-ex",
        base_url: Optional[str] = None,
        fallback_url: Optional[str] = None,
        **kwargs
    ):
        """
        @param source: source language to translate from
        @param target: target language to translate to
        @param proxies: optional proxies dictionary
        @param session: optional requests.Session or ResilientSession
        @param client: client query identifier for client API (default: "dict-chrome-ex")
        @param base_url: custom base URL endpoint (default: clients5.google.com client API)
        @param fallback_url: custom fallback URL endpoint (default: translate.googleapis.com)
        """
        self.proxies = proxies
        self.client = client
        self._fallback_url = fallback_url or BASE_URLS.get("GOOGLE_TRANSLATE_CLIENT_FALLBACK")
        effective_base_url = base_url or BASE_URLS.get("GOOGLE_TRANSLATE_CLIENT") or BASE_URLS.get("GOOGLE_TRANSLATE")

        super().__init__(
            base_url=effective_base_url,
            source=source,
            target=target,
            element_tag="div",
            element_query={"class": "t0"},
            payload_key="q",  # key of text in the url
            session=session,
            **kwargs
        )

        if self.client and self._base_url and "translate_a" in self._base_url:
            self._url_params["client"] = self.client

        self._alt_element_query = {"class": "result-container"}

    def _parse_client_json(self, data: Any) -> Optional[str]:
        """
        Extract translation string from Google client API JSON data.
        Supports:
        - clients5 explicit sl: ["translated string"]
        - clients5 sl=auto: [["translated string", "detected_lang"]]
        - translate.googleapis.com single: [[["translated", "orig", ...]], ...]
        """
        if not isinstance(data, list) or not data:
            return None

        # Case 1: clients5 flat list of strings
        if all(isinstance(item, str) for item in data):
            return "".join(data)

        # Case 2: nested structure
        if isinstance(data[0], list) and data[0]:
            # Subcase 2a: translate.googleapis.com format: [[["trans1", ...], ["trans2", ...]]]
            if isinstance(data[0][0], list):
                parts = []
                for item in data[0]:
                    if isinstance(item, list) and len(item) > 0 and isinstance(item[0], str):
                        parts.append(item[0])
                if parts:
                    return "".join(parts)

            # Subcase 2b: clients5 with sl=auto: [["translated text", "detected_lang"]]
            if isinstance(data[0][0], str):
                return data[0][0]

        return None

    def _extract_translation(self, text_content: str, original_text: str) -> str:
        """
        Extract translated text from response body, attempting JSON decoding first
        and falling back to BeautifulSoup DOM inspection.
        """
        if not text_content or not text_content.strip():
            raise TranslationNotFound(original_text)

        # 1. Try decoding JSON
        try:
            data = json.loads(text_content)
            parsed = self._parse_client_json(data)
            if parsed is not None:
                return parsed
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        # 2. Fallback to BeautifulSoup HTML DOM parsing
        soup = BeautifulSoup(text_content, "html.parser")
        element = soup.find(self._element_tag, self._element_query)
        if not element:
            element = soup.find(self._element_tag, self._alt_element_query)
            if not element:
                raise TranslationNotFound(original_text)
        return element.get_text(strip=True)

    def _check_google_body(self, response) -> None:
        from deep_translator.net import TransientResponseError
        if not response.text or not response.text.strip():
            raise TransientResponseError("Empty response body from Google Translate", response=response)

        # Check for truncated AHK token
        text_to_check = response.text.strip()
        last_open = text_to_check.rfind('[[')
        if last_open != -1:
            last_close = text_to_check.rfind(']]')
            if last_close < last_open:
                raise TransientResponseError("Truncated AHK token in response text", response=response)
        if text_to_check.endswith('['):
            raise TransientResponseError("Truncated AHK token in response text", response=response)

        # 1. Check if valid JSON translation
        try:
            data = json.loads(text_to_check)
            if self._parse_client_json(data) is not None:
                return
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        # 2. Check HTML elements
        soup = BeautifulSoup(text_to_check, "html.parser")
        element = soup.find(self._element_tag, self._element_query)
        if not element:
            element = soup.find(self._element_tag, self._alt_element_query)
            if not element:
                raise TransientResponseError("No translation element or valid JSON found in Google Translate response", response=response)

    def translate(self, text: str, **kwargs) -> str:
        """
        function to translate a text
        @param text: desired text to translate
        @return: str: translated text
        """
        import time
        import urllib.error
        from deep_translator.net import TransientResponseError

        if is_input_valid(text, max_chars=5000):
            text = text.strip()
            if self._same_source_target() or is_empty(text):
                return text

            start_time = time.time()
            use_post = len(text) > 2000

            base_params = dict(self._url_params)
            base_params["tl"] = self._target
            base_params["sl"] = self._source

            def _prepare_request_args(url: str):
                req_params = dict(base_params)
                if "translate_a" in url and "client" not in req_params and self.client:
                    req_params["client"] = self.client
                if "single" in url and "dt" not in req_params:
                    req_params["dt"] = "t"

                if use_post:
                    req_data = {"q": text}
                    req_params.pop("q", None)
                    return req_params, req_data
                else:
                    req_params["q"] = text
                    return req_params, None

            def _send_request(url: str, max_total_time: Optional[float] = None, once: bool = False):
                req_params, req_data = _prepare_request_args(url)
                if use_post:
                    if once:
                        session = self._get_resilient_session()
                        return session.request_once(
                            "POST",
                            url,
                            data=req_data,
                            params=req_params,
                            timeout=self._timeout,
                            check_response=self._check_google_body,
                            max_total_time=max_total_time,
                            on_retry=self._on_retry,
                        )
                    return self._http_post(
                        url,
                        data=req_data,
                        params=req_params,
                        check_response=self._check_google_body,
                        max_total_time=max_total_time,
                    )
                else:
                    if once:
                        return self._http_get_once(
                            url,
                            params=req_params,
                            check_response=self._check_google_body,
                            max_total_time=max_total_time,
                        )
                    return self._http_get(
                        url,
                        params=req_params,
                        check_response=self._check_google_body,
                        max_total_time=max_total_time,
                    )

            endpoints_to_try = [self._base_url]
            if self._fallback_url and self._fallback_url != self._base_url:
                endpoints_to_try.append(self._fallback_url)

            last_exception = None
            response = None

            for idx, endpoint in enumerate(endpoints_to_try):
                remaining_time = None
                if self._max_total_time is not None:
                    elapsed = time.time() - start_time
                    remaining_time = max(0.0, self._max_total_time - elapsed)
                    if remaining_time <= 0.0:
                        raise TimeoutError(f"Request deadline of {self._max_total_time}s exceeded.")

                try:
                    response = _send_request(endpoint, max_total_time=remaining_time)
                    if response is not None and not request_failed(status_code=response.status_code):
                        break
                except (TransientResponseError, TranslationNotFound, RequestError, TooManyRequests, urllib.error.URLError, urllib.error.HTTPError) as e:
                    last_exception = e
                    if idx < len(endpoints_to_try) - 1:
                        continue
                    if isinstance(e, urllib.error.HTTPError) and e.code == 429:
                        raise TooManyRequests()
                    if isinstance(e, (TransientResponseError, TranslationNotFound)):
                        raise TranslationNotFound(text)
                    if isinstance(e, TooManyRequests):
                        raise
                    raise RequestError()

            if response is None:
                if isinstance(last_exception, TooManyRequests):
                    raise TooManyRequests()
                if isinstance(last_exception, urllib.error.HTTPError) and last_exception.code == 429:
                    raise TooManyRequests()
                raise TranslationNotFound(text)

            if request_failed(status_code=response.status_code):
                if response.status_code == 429:
                    raise TooManyRequests()
                raise RequestError()

            translated = self._extract_translation(response.text, text)

            # Check identical output retry logic for legacy HTML/hl
            if translated == text.strip():
                to_translate_alpha = "".join(ch for ch in text.strip() if ch.isalnum())
                translated_alpha = "".join(ch for ch in translated if ch.isalnum())
                if to_translate_alpha and translated_alpha and to_translate_alpha == translated_alpha:
                    if "hl" in self._url_params:
                        del self._url_params["hl"]
                        base_params.pop("hl", None)
                        remaining = None
                        if self._max_total_time is not None:
                            elapsed = time.time() - start_time
                            remaining = max(0.0, self._max_total_time - elapsed)
                        try:
                            response = _send_request(self._base_url, max_total_time=remaining, once=True)
                            translated = self._extract_translation(response.text, text)
                        except (TransientResponseError, TranslationNotFound):
                            raise TranslationNotFound(text)
                        except urllib.error.HTTPError as e:
                            if e.code == 429:
                                raise TooManyRequests()
                            raise RequestError()

            return translated

    def translate_file(self, path: str, **kwargs) -> str:
        """
        translate directly from file
        @param path: path to the target file
        @type path: str
        @param kwargs: additional args
        @return: str
        """
        return self._translate_file(path, **kwargs)

    def translate_batch(self, batch: List[str], **kwargs) -> List[str]:
        """
        translate a list of texts
        @param batch: list of texts you want to translate
        @return: list of translations
        """
        return self._translate_batch(batch, **kwargs)
