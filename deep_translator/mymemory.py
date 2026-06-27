"""
mymemory translator API
"""

__copyright__ = "Copyright (C) 2020 Nidhal Baccouri"

from typing import List, Optional, Union

from deep_translator.base import BaseTranslator
from deep_translator.constants import BASE_URLS, MY_MEMORY_LANGUAGES_TO_CODES
from deep_translator.exceptions import (
    RequestError,
    TooManyRequests,
    TranslationNotFound,
)
from deep_translator.validate import is_empty, is_input_valid, request_failed


class MyMemoryTranslator(BaseTranslator):
    """
    class that uses the mymemory translator to translate texts
    """

    def __init__(
        self,
        source: str = "auto",
        target: str = "en",
        proxies: Optional[dict] = None,
        **kwargs,
    ):
        """
        @param source: source language to translate from
        @param target: target language to translate to
        """
        self.proxies = proxies
        self.email = kwargs.get("email", None)
        super().__init__(
            base_url=BASE_URLS.get("MYMEMORY"),
            source=source,
            target=target,
            payload_key="q",
            languages=MY_MEMORY_LANGUAGES_TO_CODES,
            **kwargs,
        )

    def _check_mymemory_body(self, response) -> None:
        from deep_translator.net import TransientResponseError
        if not response.text or not response.text.strip():
            raise TransientResponseError("Empty response body from MyMemory", response=response)
        
        # Check for truncated AHK token
        text_to_check = response.text.strip()
        last_open = text_to_check.rfind('[[')
        if last_open != -1:
            last_close = text_to_check.rfind(']]')
            if last_close < last_open:
                raise TransientResponseError("Truncated AHK token in response text", response=response)
        if text_to_check.endswith('['):
            raise TransientResponseError("Truncated AHK token in response text", response=response)

        try:
            data = response.json()
        except Exception as e:
            raise TransientResponseError(f"Failed to decode JSON from MyMemory: {str(e)}", response=response)

        if not data:
            raise TransientResponseError("No JSON data from MyMemory", response=response)

        res_data = data.get("responseData")
        if not res_data or "translatedText" not in res_data:
            if not data.get("matches"):
                raise TransientResponseError("Missing responseData/translatedText and no matches in MyMemory response", response=response)

    def translate(
        self, text: str, return_all: bool = False, **kwargs
    ) -> Union[str, List[str]]:
        """
        function that uses the mymemory translator to translate a text
        @param text: desired text to translate
        @type text: str
        @param return_all: set to True to return all synonym/similars of the translated text
        @return: str or list
        """
        import urllib.error
        from deep_translator.net import TransientResponseError
        if is_input_valid(text, max_chars=500):
            text = text.strip()
            if self._same_source_target() or is_empty(text):
                return text

            self._url_params["langpair"] = f"{self._source}|{self._target}"
            if self.payload_key:
                self._url_params[self.payload_key] = text
            if self.email:
                self._url_params["de"] = self.email

            try:
                response = self._http_get(
                    self._base_url,
                    params=self._url_params,
                    check_response=self._check_mymemory_body,
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

            data = response.json()
            if not data:
                raise TranslationNotFound(text)

            translation = data.get("responseData").get("translatedText")
            all_matches = data.get("matches", [])

            if translation:
                if not return_all:
                    return translation
                else:
                    return [translation] + list(all_matches)

            elif not translation:
                matches = (match["translation"] for match in all_matches)
                try:
                    next_match = next(matches)
                    return next_match if not return_all else list(all_matches)
                except StopIteration:
                    raise TranslationNotFound(text)

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
