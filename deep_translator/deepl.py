__copyright__ = "Copyright (C) 2020 Nidhal Baccouri"

import os
from typing import List, Optional

from deep_translator.base import BaseTranslator
from deep_translator.constants import (
    BASE_URLS,
    DEEPL_ENV_VAR,
    DEEPL_LANGUAGE_TO_CODE,
)
from deep_translator.exceptions import (
    ApiKeyException,
    AuthorizationException,
    ServerException,
    TranslationNotFound,
)
from deep_translator.validate import is_empty, is_input_valid, request_failed


class DeeplTranslator(BaseTranslator):
    """
    class that wraps functions, which use the DeeplTranslator translator
    under the hood to translate word(s)
    """

    def __init__(
        self,
        source: str = "de",
        target: str = "en",
        api_key: Optional[str] = os.getenv(DEEPL_ENV_VAR, None),
        use_free_api: bool = True,
        **kwargs
    ):
        """
        @param api_key: your DeeplTranslator api key.
        Get one here: https://www.deepl.com/docs-api/accessing-the-api/
        @param source: source language
        @param target: target language
        """
        if not api_key:
            raise ApiKeyException(env_var=DEEPL_ENV_VAR)

        self.version = "v2"
        self.api_key = api_key
        self.proxies = kwargs.get("proxies", None)
        url = (
            BASE_URLS.get("DEEPL_FREE").format(version=self.version)
            if use_free_api
            else BASE_URLS.get("DEEPL").format(version=self.version)
        )
        super().__init__(
            base_url=url,
            source=source,
            target=target,
            languages=DEEPL_LANGUAGE_TO_CODE,
            **kwargs
        )

    def _check_deepl_body(self, response) -> None:
        from deep_translator.net import TransientResponseError
        if not response.text or not response.text.strip():
            raise TransientResponseError("Empty response body from DeepL", response=response)
        
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
            res = response.json()
        except Exception as e:
            raise TransientResponseError(f"Failed to decode JSON from DeepL: {str(e)}", response=response)
        if not res or "translations" not in res or not res["translations"] or "text" not in res["translations"][0]:
            raise TransientResponseError("Missing translations or text in DeepL response JSON", response=response)

    def translate(self, text: str, **kwargs) -> str:
        """
        @param text: text to translate
        @return: translated text
        """
        import urllib.error
        import socket
        from deep_translator.net import TransientResponseError
        if is_input_valid(text):
            if self._same_source_target() or is_empty(text):
                return text

            # Create the request parameters.
            translate_endpoint = "translate"
            headers = {
                "Authorization": f"DeepL-Auth-Key {self.api_key}"
            }
            data = {
                "source_lang": self._source,
                "target_lang": self._target,
                "text": text,
            }
            # Do the request and check the connection.
            try:
                response = self._http_post(
                    self._base_url + translate_endpoint,
                    data=data,
                    headers=headers,
                    check_response=self._check_deepl_body,
                )
            except (urllib.error.URLError, socket.timeout, ConnectionError, TimeoutError):
                raise ServerException(503)
            except urllib.error.HTTPError as e:
                if e.code == 429 or (500 <= e.code <= 504):
                    raise ServerException(e.code)
                raise ServerException(500)
            except TransientResponseError:
                raise TranslationNotFound(text)

            # Check returned non-transient error status codes
            if response.status_code == 403:
                raise AuthorizationException(self.api_key)
            elif request_failed(status_code=response.status_code):
                raise ServerException(response.status_code)

            # Get the response and check is not empty.
            res = response.json()
            if not res:
                raise TranslationNotFound(text)
            # Process and return the response.
            return res["translations"][0]["text"]

    def translate_file(self, path: str, **kwargs) -> str:
        return self._translate_file(path, **kwargs)

    def translate_batch(self, batch: List[str], **kwargs) -> List[str]:
        """
        @param batch: list of texts to translate
        @return: list of translations
        """
        return self._translate_batch(batch, **kwargs)


if __name__ == "__main__":
    d = DeeplTranslator(target="en", api_key="some-key")
    t = d.translate("Ich habe keine ahnung")
    print("text: ", t)
