import pytest
from pydantic import ValidationError

from src import settings
from src.settings import _Settings


# --- trigger / fetch-cap wiring ---

def default_of(name: str):
    """The declared default, not the resolved value.

    All four of these are configmap-wired, so a resolved value only says what this
    machine's environment holds. The claim under test is about what ships.
    """
    return _Settings.model_fields[name].default


def test_triggers_and_caps_ship_with_headroom():
    """A cap below its trigger leaves a remainder every cycle. The defaults must not."""
    assert default_of('MEMORY_TRIGGER_SIZE') == 40
    assert default_of('EMBEDDINGS_TRIGGER_SIZE') == 40
    assert default_of('MESSAGES_MEMORY_MAX_SIZE') == 60
    assert default_of('MESSAGES_EMBEDDINGS_MAX_SIZE') == 60
    assert default_of('MESSAGES_MEMORY_MAX_SIZE') > default_of('MEMORY_TRIGGER_SIZE')
    assert default_of('MESSAGES_EMBEDDINGS_MAX_SIZE') > default_of('EMBEDDINGS_TRIGGER_SIZE')


def test_the_deployment_always_satisfies_the_validator():
    """Whatever this environment holds, the running config is self-consistent."""
    assert settings.MESSAGES_MEMORY_MAX_SIZE >= settings.MEMORY_TRIGGER_SIZE
    assert settings.MESSAGES_EMBEDDINGS_MAX_SIZE >= settings.EMBEDDINGS_TRIGGER_SIZE


def test_triggers_are_re_exported_at_module_level():
    """The codebase reads `settings.NAME`, and the tests patch it there."""
    assert settings.MEMORY_TRIGGER_SIZE == settings._s.MEMORY_TRIGGER_SIZE
    assert settings.EMBEDDINGS_TRIGGER_SIZE == settings._s.EMBEDDINGS_TRIGGER_SIZE


# --- _fetch_caps_exceed_triggers ---

def test_memory_cap_below_its_trigger_refuses_to_boot():
    with pytest.raises(ValidationError) as excinfo:
        _Settings(MESSAGES_MEMORY_MAX_SIZE=10, MEMORY_TRIGGER_SIZE=40)

    assert 'MESSAGES_MEMORY_MAX_SIZE must be >= MEMORY_TRIGGER_SIZE' in str(excinfo.value)


def test_embeddings_cap_below_its_trigger_refuses_to_boot():
    with pytest.raises(ValidationError) as excinfo:
        _Settings(MESSAGES_EMBEDDINGS_MAX_SIZE=10, EMBEDDINGS_TRIGGER_SIZE=40)

    assert 'MESSAGES_EMBEDDINGS_MAX_SIZE must be >= EMBEDDINGS_TRIGGER_SIZE' in str(excinfo.value)


def test_cap_equal_to_its_trigger_is_allowed():
    """Zero headroom costs an extra cycle, not a lost message. Not worth refusing."""
    resolved = _Settings(
        MESSAGES_MEMORY_MAX_SIZE=50,
        MEMORY_TRIGGER_SIZE=50,
        MESSAGES_EMBEDDINGS_MAX_SIZE=50,
        EMBEDDINGS_TRIGGER_SIZE=50,
    )

    assert resolved.MESSAGES_MEMORY_MAX_SIZE == 50
    assert resolved.MEMORY_TRIGGER_SIZE == 50


def test_the_validator_raises_rather_than_clamping():
    """Clamping would restore the silent misconfiguration this pair exists to remove."""
    with pytest.raises(ValidationError):
        _Settings(MESSAGES_MEMORY_MAX_SIZE=39, MEMORY_TRIGGER_SIZE=40)
