"""Top-level package for Deep Translator"""

__copyright__ = "Copyright (C) 2020 Nidhal Baccouri"
__author__ = """Nidhal Baccouri"""
__email__ = "nidhalbacc@gmail.com"
__version__ = "1.9.1"

import importlib

__all__ = [
    "GoogleTranslator",
    "PonsTranslator",
    "LingueeTranslator",
    "MyMemoryTranslator",
    "YandexTranslator",
    "MicrosoftTranslator",
    "QcriTranslator",
    "DeeplTranslator",
    "LibreTranslator",
    "PapagoTranslator",
    "ChatGptTranslator",
    "TencentTranslator",
    "BaiduTranslator",
    "single_detection",
    "batch_detection",
]

_lazy_mapping = {
    "GoogleTranslator": ".google",
    "PonsTranslator": ".pons",
    "LingueeTranslator": ".linguee",
    "MyMemoryTranslator": ".mymemory",
    "YandexTranslator": ".yandex",
    "MicrosoftTranslator": ".microsoft",
    "QcriTranslator": ".qcri",
    "DeeplTranslator": ".deepl",
    "LibreTranslator": ".libre",
    "PapagoTranslator": ".papago",
    "ChatGptTranslator": ".chatgpt",
    "TencentTranslator": ".tencent",
    "BaiduTranslator": ".baidu",
    "single_detection": ".detection",
    "batch_detection": ".detection",
}

def __getattr__(name: str):
    if name in _lazy_mapping:
        module_path = _lazy_mapping[name]
        module = importlib.import_module(module_path, __package__)
        val = getattr(module, name)
        globals()[name] = val
        return val
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")

def __dir__():
    return __all__
