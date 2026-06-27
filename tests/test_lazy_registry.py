import subprocess
import sys
import pytest

def run_in_fresh_process(code: str):
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert res.returncode == 0, f"Code failed with stderr:\n{res.stderr}\n\nStdout:\n{res.stdout}"

def test_no_eager_imports_on_package_load():
    code = """
import sys
import deep_translator
engines = ["google", "pons", "linguee", "mymemory", "yandex", "microsoft", "qcri", "deepl", "libre", "papago", "chatgpt", "tencent", "baidu", "detection"]
for engine in engines:
    assert f"deep_translator.{engine}" not in sys.modules, f"{engine} was eagerly imported"
"""
    run_in_fresh_process(code)

def test_single_engine_import_only_loads_that_engine():
    code = """
import sys
from deep_translator import GoogleTranslator
assert "deep_translator.google" in sys.modules
other_engines = ["pons", "linguee", "mymemory", "yandex", "microsoft", "qcri", "deepl", "libre", "papago", "chatgpt", "tencent", "baidu", "detection"]
for engine in other_engines:
    assert f"deep_translator.{engine}" not in sys.modules, f"{engine} was imported"
"""
    run_in_fresh_process(code)

def test_requests_not_imported_for_google_translator():
    code = """
import sys
from deep_translator import GoogleTranslator
assert "requests" not in sys.modules
"""
    run_in_fresh_process(code)

def test_dir_does_not_trigger_imports():
    code = """
import sys
import deep_translator
names = dir(deep_translator)
# Ensure all __all__ names are listed
for name in deep_translator.__all__:
    assert name in names

engines = ["google", "pons", "linguee", "mymemory", "yandex", "microsoft", "qcri", "deepl", "libre", "papago", "chatgpt", "tencent", "baidu", "detection"]
for engine in engines:
    assert f"deep_translator.{engine}" not in sys.modules
"""
    run_in_fresh_process(code)

def test_star_import_exposes_all_names():
    code = """
from deep_translator import *
# Just reference them to check they are in scope
_ = GoogleTranslator
_ = PonsTranslator
_ = LingueeTranslator
_ = MyMemoryTranslator
_ = YandexTranslator
_ = MicrosoftTranslator
_ = QcriTranslator
_ = DeeplTranslator
_ = LibreTranslator
_ = PapagoTranslator
_ = ChatGptTranslator
_ = TencentTranslator
_ = BaiduTranslator
_ = single_detection
_ = batch_detection
"""
    run_in_fresh_process(code)

def test_baidu_translator_imports_requests():
    code = """
import sys
from deep_translator import BaiduTranslator
assert "deep_translator.baidu" in sys.modules
assert "requests" in sys.modules
"""
    run_in_fresh_process(code)

def test_detection_imports_requests():
    code = """
import sys
from deep_translator import single_detection
assert "deep_translator.detection" in sys.modules
assert "requests" in sys.modules
"""
    run_in_fresh_process(code)

def test_nonexistent_name_raises_attribute_error():
    with pytest.raises(AttributeError):
        import deep_translator
        _ = deep_translator.NonexistentName

def test_repeated_access_cached():
    import deep_translator
    g1 = deep_translator.GoogleTranslator
    g2 = deep_translator.GoogleTranslator
    assert g1 is g2
