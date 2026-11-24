"""Shared fixtures that change behavior for speed stay isolated per test (JAN-311)."""

from __future__ import annotations

import pytest

from janus import security
from janus.security import security_service


def test_ordinary_tests_hash_at_the_fast_cost():
    """Unmarked tests hash at bcrypt cost 4 through the production code path."""
    hashed = security_service.get_password_hash("secret")
    assert hashed.startswith("$2b$04$")
    assert security_service.verify_password("secret", hashed)
    assert not security_service.verify_password("other", hashed)


@pytest.mark.production_password_cost
def test_marked_tests_keep_the_production_cost():
    """The marker restores the real cost, so auth regressions measure production hashing."""
    assert security._BCRYPT_ROUNDS == 12
    assert security_service.get_password_hash("secret").startswith("$2b$12$")


def test_fast_cost_hashes_verify_at_production_settings(monkeypatch):
    """A hash made at the test cost still verifies once the production cost is back (cost is in the hash)."""
    hashed = security_service.get_password_hash("secret")
    monkeypatch.setattr(security, "_BCRYPT_ROUNDS", 12)
    assert security_service.verify_password("secret", hashed)
