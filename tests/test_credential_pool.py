from concurrent.futures import ThreadPoolExecutor

import pytest

from app.services.credential_pool import CredentialPool, CredentialsUnavailable


def test_gemini_pool_rotates_eight_keys_and_wraps() -> None:
    pool = CredentialPool([f"gemini-{index}" for index in range(1, 9)])

    selected = [pool.select().number for _ in range(9)]

    assert selected == [1, 2, 3, 4, 5, 6, 7, 8, 1]


def test_groq_pool_rotates_twelve_keys_and_wraps() -> None:
    pool = CredentialPool([f"groq-{index}" for index in range(1, 13)])

    selected = [pool.select().number for _ in range(13)]

    assert selected == [*range(1, 13), 1]


@pytest.mark.parametrize(
    ("keys", "expected_number"),
    [(["only"], 1), (["", "only", "  "], 2)],
)
def test_pool_supports_one_usable_key_and_ignores_blanks(
    keys: list[str], expected_number: int
) -> None:
    pool = CredentialPool(keys)

    assert [pool.select().number for _ in range(2)] == [expected_number, expected_number]


def test_pool_preserves_numbered_gaps() -> None:
    pool = CredentialPool.from_numbered({1: "first", 3: "third", 8: "eighth"})

    assert [pool.select().number for _ in range(4)] == [1, 3, 8, 1]


def test_unhealthy_key_is_skipped_only_on_future_selections() -> None:
    pool = CredentialPool.from_numbered({1: "bad", 2: "good"}, cooldown_seconds=60)
    selected = pool.select(now=10)

    pool.mark_unhealthy(selected, now=10)

    assert selected.number == 1
    assert pool.select(now=11).number == 2
    assert pool.select(now=71).number == 1


def test_all_unhealthy_credentials_fail_without_exposing_keys() -> None:
    pool = CredentialPool(["super-secret"])
    selected = pool.select(now=10)
    pool.mark_unhealthy(selected, now=10)

    with pytest.raises(CredentialsUnavailable) as caught:
        pool.select(now=11)

    assert "super-secret" not in str(caught.value)
    assert "super-secret" not in repr(pool)
    assert "super-secret" not in repr(selected)


def test_round_robin_selection_is_concurrency_safe() -> None:
    pool = CredentialPool([f"key-{index}" for index in range(1, 9)])

    with ThreadPoolExecutor(max_workers=16) as executor:
        selected = list(executor.map(lambda _index: pool.select().number, range(800)))

    assert {number: selected.count(number) for number in range(1, 9)} == {
        number: 100 for number in range(1, 9)
    }
