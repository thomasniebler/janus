"""Tests for ReDoS protection and regex validation."""

import time

from janus import models
from janus.processor import EmailProcessor


def test_safe_search_timeout():
    """Tests that rule matching completes quickly for malicious-looking patterns."""
    redos_pattern = r"(a+)+$"
    text = "a" * 25 + "!"

    processor = EmailProcessor()

    started_at = time.perf_counter()
    result = processor._safe_search(redos_pattern, text, timeout=0.1)
    elapsed = time.perf_counter() - started_at

    assert result is False
    assert elapsed < 0.1


def test_safe_search_is_case_insensitive():
    """Rule matching should stay case-insensitive when using re2's options API."""
    processor = EmailProcessor()

    assert processor._safe_search(r"hello", "HeLLo world") is True


def test_validate_regex():
    """Tests that the validate_regex helper accepts re2-compatible patterns."""
    from janus.main import validate_regex

    # Safe patterns
    assert validate_regex(r"hello") is True
    assert validate_regex(r"^[a-zA-Z0-9]+$") is True

    # re2-compatible patterns remain valid even if they would be risky in backtracking engines
    assert validate_regex(r"(a+)+$") is True
    assert validate_regex(r"((a|b)+)*$") is True

    # Invalid syntax is still rejected
    assert validate_regex(r"[unterminated") is False


# ---------------------------------------------------------------------------
# JAN-295: owner-scoped rule evaluation keeps every evaluated rule behind
# _safe_search and never evaluates another user's pattern for this user's mail.
# ---------------------------------------------------------------------------

_CATASTROPHIC = r"(a+)+$"


def _owner_with_account(db, username: str):
    user = models.User(username=username, hashed_password="hash")
    db.add(user)
    db.commit()
    account = models.EmailAccount(name=f"acct-{username}", email=f"{username}@example.com", user_id=user.id)
    db.add(account)
    db.commit()
    return user, account


def _add_rule(db, user_id, pattern: str, priority, *, order: int, field: str = "sender") -> None:
    db.add(
        models.Rule(
            name=f"{user_id}-{order}",
            pattern=pattern,
            field=field,
            priority=priority,
            is_active=True,
            order=order,
            user_id=user_id,
        )
    )
    db.commit()


def _spy_safe_search(monkeypatch) -> list[str]:
    """Record every pattern handed to ``_safe_search`` while keeping its real behavior."""
    seen: list[str] = []
    real = EmailProcessor._safe_search

    def spy(self, pattern, text, timeout=1.0):
        seen.append(pattern)
        return real(self, pattern, text, timeout)

    monkeypatch.setattr(EmailProcessor, "_safe_search", spy)
    return seen


def _mail(account, sender: str, subject: str = "Hello") -> dict:
    return {"message_id": f"<m-{account.id}@t>", "account_id": account.id, "sender": sender, "subject": subject}


def test_another_users_catastrophic_pattern_is_never_evaluated_for_this_users_mail(db, monkeypatch):
    """Alice's ReDoS-shaped rule never reaches ``_safe_search`` for Bob's mail; Bob's own rule decides."""
    alice, _ = _owner_with_account(db, "alice")
    bob, bob_account = _owner_with_account(db, "bob")
    _add_rule(db, alice.id, _CATASTROPHIC, models.Priority.SPAM, order=0)
    _add_rule(db, bob.id, r"^a+!$", models.Priority.IMPORTANT, order=5)
    seen = _spy_safe_search(monkeypatch)

    started_at = time.perf_counter()
    priority = EmailProcessor(db)._evaluate_rules(_mail(bob_account, "a" * 25 + "!"))
    elapsed = time.perf_counter() - started_at

    assert priority == models.Priority.IMPORTANT
    assert seen == [r"^a+!$"]
    assert elapsed < 0.5


def test_owner_rules_go_through_safe_search(db, monkeypatch):
    """The owner's own rules apply only via ``_safe_search``: ReDoS-shaped and invalid ones fail safe."""
    bob, bob_account = _owner_with_account(db, "bob")
    _add_rule(db, bob.id, _CATASTROPHIC, models.Priority.SPAM, order=0)
    _add_rule(db, bob.id, r"[unterminated", models.Priority.SPAM, order=1)
    _add_rule(db, bob.id, r"^digest@", models.Priority.DIGEST, order=2)
    seen = _spy_safe_search(monkeypatch)
    proc = EmailProcessor(db)

    started_at = time.perf_counter()
    assert proc._evaluate_rules(_mail(bob_account, "a" * 25 + "!")) is None
    assert time.perf_counter() - started_at < 0.5
    assert seen == [_CATASTROPHIC, r"[unterminated", r"^digest@"]

    assert proc._evaluate_rules(_mail(bob_account, "digest@news.example")) == models.Priority.DIGEST


def test_case_varying_patterns_stay_case_insensitive_and_owner_scoped(db):
    """A case-varying pattern matches its owner's mail in any case, and never another user's mail."""
    alice, alice_account = _owner_with_account(db, "alice")
    bob, bob_account = _owner_with_account(db, "bob")
    _add_rule(db, alice.id, r"NEWS@Shared\.EXAMPLE", models.Priority.IGNORE, order=0)
    _add_rule(db, bob.id, r"URGENT", models.Priority.IMPORTANT, order=1, field="subject")
    proc = EmailProcessor(db)

    assert proc._evaluate_rules(_mail(alice_account, "news@shared.example")) == models.Priority.IGNORE
    assert proc._evaluate_rules(_mail(bob_account, "News@SHARED.example")) is None
    assert proc._evaluate_rules(_mail(bob_account, "x@y.example", subject="urgent: reply")) == models.Priority.IMPORTANT
