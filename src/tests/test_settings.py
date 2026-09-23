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
    assert default_of('INITIATIVE_RUN_MESSAGES_MAX_SIZE') == 50
    assert default_of('INITIATIVE_TRIGGER_SIZE') == 5
    assert default_of('INITIATIVE_RUN_MESSAGES_MAX_SIZE') >= default_of('INITIATIVE_TRIGGER_SIZE')


def test_the_deployment_always_satisfies_the_validator():
    """Whatever this environment holds, the running config is self-consistent."""
    assert settings.MESSAGES_MEMORY_MAX_SIZE >= settings.MEMORY_TRIGGER_SIZE
    assert settings.MESSAGES_EMBEDDINGS_MAX_SIZE >= settings.EMBEDDINGS_TRIGGER_SIZE
    assert settings.INITIATIVE_RUN_MESSAGES_MAX_SIZE >= settings.INITIATIVE_TRIGGER_SIZE


def test_triggers_are_re_exported_at_module_level():
    """The codebase reads `settings.NAME`, and the tests patch it there."""
    assert settings.MEMORY_TRIGGER_SIZE == settings._s.MEMORY_TRIGGER_SIZE
    assert settings.EMBEDDINGS_TRIGGER_SIZE == settings._s.EMBEDDINGS_TRIGGER_SIZE
    assert settings.INITIATIVE_TRIGGER_SIZE == settings._s.INITIATIVE_TRIGGER_SIZE
    assert settings.INITIATIVE_CONTEXT_SIZE == settings._s.INITIATIVE_CONTEXT_SIZE
    assert settings.INITIATIVE_GAP_MINUTES == settings._s.INITIATIVE_GAP_MINUTES
    assert settings.INITIATIVE_DAILY_LIMIT == settings._s.INITIATIVE_DAILY_LIMIT


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


def test_initiative_cap_below_its_trigger_refuses_to_boot():
    # Known gap closed: nothing used to tie INITIATIVE_RUN_MESSAGES_MAX_SIZE to
    # INITIATIVE_TRIGGER_SIZE, so a bad pair made pre_check fail forever, silently.
    with pytest.raises(ValidationError) as excinfo:
        _Settings(INITIATIVE_RUN_MESSAGES_MAX_SIZE=1, INITIATIVE_TRIGGER_SIZE=40)

    assert 'INITIATIVE_RUN_MESSAGES_MAX_SIZE must be >= INITIATIVE_TRIGGER_SIZE' in str(
        excinfo.value
    )


# --- initiative numeric bounds ---
# A zero trigger clears `pre_check`'s len() gate for an empty candidate list too,
# which would then crash `candidates[-1]` in `_claim_window`. A zero-or-negative
# gap collapses every window to its single newest message. Field bounds refuse
# both at boot rather than at the first affected run.


def test_initiative_trigger_size_must_be_at_least_one():
    with pytest.raises(ValidationError):
        _Settings(INITIATIVE_TRIGGER_SIZE=0)


def test_initiative_run_messages_max_size_must_be_at_least_one():
    with pytest.raises(ValidationError):
        _Settings(INITIATIVE_RUN_MESSAGES_MAX_SIZE=0)


def test_initiative_context_size_allows_zero_but_not_negative():
    assert _Settings(INITIATIVE_CONTEXT_SIZE=0).INITIATIVE_CONTEXT_SIZE == 0
    with pytest.raises(ValidationError):
        _Settings(INITIATIVE_CONTEXT_SIZE=-1)


def test_initiative_gap_minutes_must_be_positive():
    with pytest.raises(ValidationError):
        _Settings(INITIATIVE_GAP_MINUTES=0)
    with pytest.raises(ValidationError):
        _Settings(INITIATIVE_GAP_MINUTES=-5)


def test_initiative_daily_limit_must_be_at_least_one():
    # 0 would be a second, silent kill switch — INITIATIVE_ENABLED already owns that job.
    with pytest.raises(ValidationError):
        _Settings(INITIATIVE_DAILY_LIMIT=0)
