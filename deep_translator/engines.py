__copyright__ = "Copyright (C) 2020 Nidhal Baccouri"

import collections.abc
import importlib

class LazyEnginesMapping(collections.abc.Mapping):
    def __init__(self):
        self._engines_info = {
            "google": (".google", "GoogleTranslator"),
            "pons": (".pons", "PonsTranslator"),
            "linguee": (".linguee", "LingueeTranslator"),
            "mymemory": (".mymemory", "MyMemoryTranslator"),
            "yandex": (".yandex", "YandexTranslator"),
            "microsoft": (".microsoft", "MicrosoftTranslator"),
            "qcri": (".qcri", "QcriTranslator"),
            "deepl": (".deepl", "DeeplTranslator"),
            "libre": (".libre", "LibreTranslator"),
            "papago": (".papago", "PapagoTranslator"),
            "chatgpt": (".chatgpt", "ChatGptTranslator"),
            "tencent": (".tencent", "TencentTranslator"),
            "baidu": (".baidu", "BaiduTranslator"),
        }
        self._cache = {}

    def __getitem__(self, key):
        if key not in self._engines_info:
            raise KeyError(key)
        if key not in self._cache:
            module_path, class_name = self._engines_info[key]
            module = importlib.import_module(module_path, "deep_translator")
            self._cache[key] = getattr(module, class_name)
        return self._cache[key]

    def __contains__(self, key):
        return key in self._engines_info

    def __iter__(self):
        return iter(self._engines_info)

    def __len__(self):
        return len(self._engines_info)

    def keys(self):
        return self._engines_info.keys()

__engines__ = LazyEnginesMapping()
