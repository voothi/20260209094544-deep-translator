"""
google translator API
"""

__copyright__ = "Copyright (C) 2020 Nidhal Baccouri"

from typing import List, Optional

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
        **kwargs
    ):
        """
        @param source: source language to translate from
        @param target: target language to translate to
        """
        self.proxies = proxies
        super().__init__(
            base_url=BASE_URLS.get("GOOGLE_TRANSLATE"),
            source=source,
            target=target,
            element_tag="div",
            element_query={"class": "t0"},
            payload_key="q",  # key of text in the url
            **kwargs
        )

        self._alt_element_query = {"class": "result-container"}

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

        soup = BeautifulSoup(response.text, "html.parser")
        element = soup.find(self._element_tag, self._element_query)
        if not element:
            element = soup.find(self._element_tag, self._alt_element_query)
            if not element:
                raise TransientResponseError("No translation element found in Google Translate response", response=response)

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
            self._url_params["tl"] = self._target
            self._url_params["sl"] = self._source

            if self.payload_key:
                self._url_params[self.payload_key] = text

            start_time = time.time()
            try:
                response = self._http_get(
                    self._base_url,
                    params=self._url_params,
                    check_response=self._check_google_body,
                )
            except TransientResponseError:
                raise TranslationNotFound(text)
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    raise TooManyRequests()
                raise RequestError()

            if request_failed(status_code=response.status_code):
                if response.status_code == 429:
                    raise TooManyRequests()
                raise RequestError()

            soup = BeautifulSoup(response.text, "html.parser")
            element = soup.find(self._element_tag, self._element_query)

            if not element:
                element = soup.find(self._element_tag, self._alt_element_query)
                if not element:
                    raise TranslationNotFound(text)

            if element.get_text(strip=True) == text.strip():
                to_translate_alpha = "".join(
                    ch for ch in text.strip() if ch.isalnum()
                )
                translated_alpha = "".join(
                    ch for ch in element.get_text(strip=True) if ch.isalnum()
                )
                if (
                    to_translate_alpha
                    and translated_alpha
                    and to_translate_alpha == translated_alpha
                ):
                    self._url_params["tl"] = self._target
                    if "hl" not in self._url_params:
                        return text.strip()
                    del self._url_params["hl"]

                    remaining = None
                    if self._max_total_time is not None:
                        elapsed = time.time() - start_time
                        remaining = max(0.0, self._max_total_time - elapsed)

                    try:
                        response = self._http_get_once(
                            self._base_url,
                            params=self._url_params,
                            check_response=self._check_google_body,
                            max_total_time=remaining,
                        )
                    except TransientResponseError:
                        raise TranslationNotFound(text)
                    except urllib.error.HTTPError as e:
                        if e.code == 429:
                            raise TooManyRequests()
                        raise RequestError()

                    if request_failed(status_code=response.status_code):
                        if response.status_code == 429:
                            raise TooManyRequests()
                        raise RequestError()

                    soup = BeautifulSoup(response.text, "html.parser")
                    element = soup.find(self._element_tag, self._element_query)
                    if not element:
                        element = soup.find(self._element_tag, self._alt_element_query)
                        if not element:
                            raise TranslationNotFound(text)
                    return element.get_text(strip=True)
            else:
                return element.get_text(strip=True)

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
