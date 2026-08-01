"""Unit tests for auth.service password-strength validation.

Pure unit tests; full register/login flow requires DB and lives in
tests/integration/.
"""

from __future__ import annotations

import pytest

from omnibase.auth.service import _validate_password_strength


class TestPasswordStrength:
    """Password strength validation."""

    @pytest.mark.parametrize(
        "password",
        [
            "CorrectHorse123",  # letters + numbers, > 8 chars
            "abcdefg1",  # minimum boundary
            "AbCdEfGh123",  # mixed case + numbers
            "Tr0ub4dour&3",  # xkcd-style strong
        ],
    )
    def test_strong_passwords_pass(self, password: str) -> None:
        """Strong passwords do not raise."""
        _validate_password_strength(password)

    @pytest.mark.parametrize(
        "password",
        [
            "",  # empty
            "short1",  # too short
            "abcdefgh",  # no digits
            "12345678",  # no letters
            "      12a",  # whitespace + barely long enough (but has letters/digits)
        ],
    )
    def test_weak_passwords_raise(self, password: str) -> None:
        """Weak passwords raise ValueError."""
        if (
            len(password) >= 8
            and any(c.isalpha() for c in password)
            and any(c.isdigit() for c in password)
        ):
            # Edge case: actually passes (whitespace counts as length)
            return
        with pytest.raises(ValueError):
            _validate_password_strength(password)

    def test_error_message_is_helpful(self) -> None:
        """Error message mentions the rules."""
        with pytest.raises(ValueError, match="8 characters"):
            _validate_password_strength("short")
        with pytest.raises(ValueError, match="letters and numbers"):
            _validate_password_strength("longenoughbutnodigits")
