import pytest

from app.config import Settings


def test_primary_configuration_requires_model_and_at_least_one_key() -> None:
    with pytest.raises(ValueError):
        Settings(_env_file=None).validate_provider_configuration()


def test_numbered_configuration_ignores_blanks_and_preserves_suffixes() -> None:
    settings = Settings(
        _env_file=None,
        gemini_model="gemini-model",
        gemini_api_key_2=" second ",
        gemini_api_key_8="eighth",
    )

    settings.validate_provider_configuration()

    assert settings.gemini_keys == {2: "second", 8: "eighth"}


def test_partial_backup_configuration_is_rejected() -> None:
    settings = Settings(
        _env_file=None,
        gemini_model="gemini-model",
        gemini_api_key_1="primary",
        groq_model="groq-model",
    )

    with pytest.raises(ValueError):
        settings.validate_provider_configuration()


def test_backup_may_be_completely_disabled() -> None:
    settings = Settings(
        _env_file=None,
        gemini_model="gemini-model",
        gemini_api_key_1="primary",
    )

    settings.validate_provider_configuration()

    assert settings.groq_keys == {}
