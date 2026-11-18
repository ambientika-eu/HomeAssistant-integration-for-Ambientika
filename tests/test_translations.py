"""Tests for the translation files.

Every language carries exactly the texts of the English file, in the same
order, and the states named there are the states the integration reports.
These tests run with nothing but pytest: ``python -m pytest tests``.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "ambientika"
TRANSLATIONS = COMPONENT / "translations"
LANGUAGES = sorted(path.stem for path in TRANSLATIONS.glob("*.json"))

_spec = importlib.util.spec_from_file_location(
    "ambientika_const", COMPONENT / "const.py"
)
const = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = const
_spec.loader.exec_module(const)


def load(language: str) -> dict:
    """Read the translation file of one language."""
    with (TRANSLATIONS / f"{language}.json").open(encoding="utf-8") as file:
        return json.load(file)


def flat(node: dict, prefix: str = "") -> list[tuple[str, object]]:
    """Return every text of a translation file with its full key, in file order."""
    texts: list[tuple[str, object]] = []
    for key, value in node.items():
        if isinstance(value, dict):
            texts.extend(flat(value, f"{prefix}{key}."))
        else:
            texts.append((f"{prefix}{key}", value))
    return texts


def test_languages() -> None:
    """The languages the integration is delivered in."""
    assert LANGUAGES == [
        "cs",
        "da",
        "de",
        "en",
        "es",
        "fr",
        "it",
        "nl",
        "pl",
        "pt",
        "sv",
    ]


@pytest.mark.parametrize("language", LANGUAGES)
def test_same_keys_as_english(language: str) -> None:
    """Every language has the keys of the English file, in the same order."""
    assert [key for key, _ in flat(load(language))] == [
        key for key, _ in flat(load("en"))
    ]


@pytest.mark.parametrize("language", LANGUAGES)
def test_texts(language: str) -> None:
    """Every text is a filled-in string without stray spaces or placeholders."""
    for key, text in flat(load(language)):
        assert isinstance(text, str), key
        assert text, key
        assert text == text.strip(), key
        assert "  " not in text, key
        assert "{" not in text, key
        assert "}" not in text, key


@pytest.mark.parametrize("language", LANGUAGES)
def test_states_are_the_states_of_the_integration(language: str) -> None:
    """The states with a text are exactly the states the sensors report."""
    sensors = load(language)["entity"]["sensor"]
    assert list(sensors["air_quality"]["state"]) == [
        state.value for state in const.AirQuality
    ]
    assert list(sensors["filter_status"]["state"]) == [
        state.value for state in const.FilterStatus
    ]


@pytest.mark.parametrize("language", LANGUAGES)
def test_file_layout(language: str) -> None:
    """Two spaces of indentation, readable characters, one line break at the end."""
    raw = (TRANSLATIONS / f"{language}.json").read_text(encoding="utf-8")
    assert raw == json.dumps(load(language), indent=2, ensure_ascii=False) + "\n"
