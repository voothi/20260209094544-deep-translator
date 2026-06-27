import subprocess
import sys
import pytest

def run_in_fresh_process(code: str):
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert res.returncode == 0, f"Code failed with stderr:\n{res.stderr}\n\nStdout:\n{res.stdout}"

def test_engines_registry_lazy_loading():
    code = """
import sys
from deep_translator.engines import __engines__

# Importing deep_translator.engines does NOT eagerly import any engine submodules
engines = ["google", "pons", "linguee", "mymemory", "yandex", "microsoft", "qcri", "deepl", "libre", "papago", "chatgpt", "tencent", "baidu"]
for engine in engines:
    assert f"deep_translator.{engine}" not in sys.modules

# Check that __engines__ has 13 engines
assert len(__engines__) == 13

# Check that keys are present
assert "google" in __engines__
assert "deepl" in __engines__
assert "nonexistent" not in __engines__

# Lookup imports GoogleTranslator on demand
google_cls = __engines__.get("google")
from deep_translator.google import GoogleTranslator
assert google_cls is GoogleTranslator
assert "deep_translator.google" in sys.modules

# Other engines still not imported
for engine in engines:
    if engine != "google":
        assert f"deep_translator.{engine}" not in sys.modules
"""
    run_in_fresh_process(code)

def test_engines_registry_get_none_for_unknown():
    from deep_translator.engines import __engines__
    assert __engines__.get("nonexistent") is None
