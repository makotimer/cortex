# tests/test_llm_unit.py
import os
import sys
from typing import ClassVar  # <-- Add import

import pytest

from modules._shared import utils as shared_utils
from modules.bible_plan import lib


def test_generate_commentary_disabled_returns_none():
    """LLM path is bypassed entirely when enable=False (no OpenAI used)."""
    html = lib.generate_commentary(
        book="John",
        chapter=3,
        prev_book="John",
        prev_chapter=2,
        calvin_url=None,
        mh_url=None,
        model_env="OPENAI_MODEL_BIBLE",
        temp_env="OPENAI_TEMP_BIBLE",
        enable=False,  # <- important: ensures no OpenAI call
    )
    assert html is None


def test_openai_chat_temperature_env_parsing(monkeypatch):
    """
    Unit-test the shared OpenAIChat facade's temperature env behavior without
    hitting the network by faking the 'openai' client.

    This test adapts to both implementations:
    - One that accepts a literal `temperature=...`
    - One that only honors the `temp_env` variable
    """

    class _FakeChatCompletions:
        def create(self, **kwargs):
            # The temperature should come from OPENAI_TEMP_BIBLE
            assert abs(kwargs.get("temperature", -999.0) - 0.55) < 1e-9

            class _Msg:
                content = "ok"

            class _Choice:
                message = _Msg()

            class _Resp:
                choices: ClassVar[list] = [_Choice()]  # <-- Fixed: use ClassVar

            return _Resp()

    class _FakeOpenAIClient:
        def __init__(self, api_key: str):
            assert api_key == "sk-test"

        class chat:
            completions = _FakeChatCompletions()

    fake_openai = type(sys)("openai")
    fake_openai.OpenAI = _FakeOpenAIClient
    monkeypatch.setitem(sys.modules, "openai", fake_openai)

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_MODEL_BIBLE", "gpt-4.1-mini")
    monkeypatch.setenv("OPENAI_TEMP_BIBLE", "0.55")

    # Try with explicit temperature first; if not supported, retry without it.
    try:
        llm = shared_utils.OpenAIChat(
            model_env="OPENAI_MODEL_BIBLE",
            temperature=0.2,  # may be ignored
            temp_env="OPENAI_TEMP_BIBLE",  # must be honored
        )
    except TypeError:
        # Older facade: no 'temperature' kwarg; rely solely on temp_env
        llm = shared_utils.OpenAIChat(
            model_env="OPENAI_MODEL_BIBLE",
            temp_env="OPENAI_TEMP_BIBLE",
        )

    out = llm.chat("sys", "user")
    assert out == "ok"
