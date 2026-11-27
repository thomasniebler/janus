"""Mailbox (email account) create, update and delete, together with each mailbox's monitor thread.

A mailbox is read by a monitor thread (``workers/email.py``). Adding or switching on a mailbox
starts it, switching it off or deleting it stops it, and changing how it connects restarts it,
so no change here needs a Janus restart.
"""

from sqlalchemy.orm import Session

from janus import models, security
from janus.services.email_service import EmailService

# Fields that decide whether and how a mailbox's monitor runs; a change to any of them (re)starts it.
_MONITOR_FIELDS = ("is_active", "email", "username", "password", "imap_server", "imap_port")


def add_account(db: Session, data: dict, user_id: int | None = None) -> models.EmailAccount:
    """Adds a mailbox and starts monitoring it when it is active."""
    from janus.workers.email import start_mailbox_worker

    if "password" in data:
        data["password"] = security.security_service.encrypt(data["password"])
    if user_id is not None:
        data["user_id"] = user_id
    account = models.EmailAccount(**data)
    db.add(account)
    db.commit()
    db.refresh(account)
    start_mailbox_worker(account)
    return account


def update_account_for_user(db: Session, account_id: int, user_id: int, data: dict) -> models.EmailAccount | None:
    """Updates a mailbox owned by ``user_id``.

    Returns ``None`` when the mailbox does not exist or belongs to another user, so callers
    answer both cases with the same non-disclosing 404. Switching it on or off starts or stops
    its monitor; changing how it connects restarts a running one.
    """
    from janus.workers.email import start_mailbox_worker, stop_mailbox_worker

    account = EmailService(db).get_account_for_user(account_id, user_id)
    if not account:
        return None
    before = {field: getattr(account, field) for field in _MONITOR_FIELDS}

    if account.auth_type != "password":
        # Gmail/Outlook mailboxes read through their connection; their server and identity are fixed.
        for locked in ("password", "imap_server", "imap_port", "username", "email"):
            data.pop(locked, None)
    password = data.pop("password", None)
    for field, value in data.items():
        setattr(account, field, value)
    if password:
        account.password = security.security_service.encrypt(password)

    db.commit()
    db.refresh(account)
    if any(getattr(account, field) != value for field, value in before.items()):
        if before["is_active"]:
            stop_mailbox_worker(account.id, before["email"])
        start_mailbox_worker(account)
    return account


def delete_account_for_user(db: Session, account_id: int, user_id: int) -> bool:
    """Removes a mailbox owned by ``user_id`` and stops its monitor; ``False`` if missing or foreign."""
    from janus.workers.email import stop_mailbox_worker

    account = EmailService(db).get_account_for_user(account_id, user_id)
    if not account:
        return False
    address = account.email
    db.delete(account)
    db.commit()
    stop_mailbox_worker(account_id, address)
    return True
