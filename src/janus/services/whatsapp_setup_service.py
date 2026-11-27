"""Transient setup-session lifecycle for WhatsApp onboarding."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from janus import models, time_utils
from janus.security import security_service
from janus.services.base import BaseService
from janus.services.whatsapp_service import WhatsAppService
from janus.whatsapp_neonize import (
    NeonizeAdapter,
    NeonizePairingCapability,
    NeonizePairingSnapshot,
    NeonizeSessionArtifact,
)

SETUP_SESSION_TTL = timedelta(minutes=15)
TERMINAL_SETUP_STATUSES = {
    models.WhatsAppSetupStatus.COMPLETED,
    models.WhatsAppSetupStatus.CANCELLED,
    models.WhatsAppSetupStatus.EXPIRED,
}
ACTIVE_PAIRING_STATES = {
    models.WhatsAppPairingState.STARTING,
    models.WhatsAppPairingState.WAITING,
    models.WhatsAppPairingState.CODE_READY,
}


class WhatsAppSetupService(BaseService):
    """Own transient onboarding state without mixing it into durable account records."""

    def create_setup_session(self, user: models.User, data: dict[str, Any]) -> models.WhatsAppSetupSession:
        """Create a user-scoped setup session for onboarding or reconnect."""
        self.expire_stale_sessions(user_id=user.id)

        account_id = data.get("account_id")
        account = None
        if account_id is not None:
            account = WhatsAppService(self.db).get_account_for_user(account_id, user)
            if account is None:
                raise ValueError("WhatsApp account not found")

        capability = NeonizeAdapter(account=account).get_pairing_capability()
        session = models.WhatsAppSetupSession(
            user_id=user.id,
            account_id=account.id if account is not None else None,
            display_name=data["display_name"].strip(),
            initial_history_days=data.get("initial_history_days", 3),
            expires_at=self._next_expiry(),
        )
        self._apply_pairing_capability(session, capability)
        self.db.add(session)
        self.db.commit()
        self.db.refresh(session)
        return session

    def get_setup_session_for_user(self, session_id: int, user: models.User) -> models.WhatsAppSetupSession | None:
        """Return one setup session only when owned by the current user."""
        self.expire_stale_sessions(user_id=user.id)
        session = (
            self.db.query(models.WhatsAppSetupSession)
            .filter(models.WhatsAppSetupSession.id == session_id, models.WhatsAppSetupSession.user_id == user.id)
            .first()
        )
        if session is None:
            return None
        if session.setup_status == models.WhatsAppSetupStatus.PAIRING:
            self.refresh_pairing_state(session)
        return session

    def start_pairing(self, session: models.WhatsAppSetupSession) -> models.WhatsAppSetupSession:
        """Start or re-start live pairing for a setup session."""
        self._ensure_not_terminal(session)
        capability = NeonizeAdapter(account=session.account).get_pairing_capability()
        self._apply_pairing_capability(session, capability)
        if not capability.supported:
            self.db.commit()
            self.db.refresh(session)
            return session

        session.setup_status = models.WhatsAppSetupStatus.PAIRING
        session.pairing_state = models.WhatsAppPairingState.STARTING
        session.pairing_message = "Starting live Neonize pairing."
        self._touch(session)
        self.db.commit()
        self.db.refresh(session)

        # begin_pairing() only reports whether the pairing thread launched; once it
        # has, that background thread owns further state transitions (WAITING,
        # CODE_READY, PAIRED, ...) via _update_setup_session_pairing. Committing
        # afterwards here as if it reflected an equal-or-later state could clobber a
        # faster background update, so only apply the snapshot when begin_pairing
        # itself failed to start pairing.
        snapshot = NeonizeAdapter(account=session.account).begin_pairing(session)
        if snapshot.state != models.WhatsAppPairingState.STARTING:
            self._apply_pairing_snapshot(session, snapshot)
            self.db.commit()
            self.db.refresh(session)
        elif snapshot.session_database_path and snapshot.session_database_path != session.session_database_path:
            session.session_database_path = snapshot.session_database_path
            self.db.commit()
            self.db.refresh(session)
        return session

    def refresh_pairing_state(self, session: models.WhatsAppSetupSession) -> models.WhatsAppSetupSession:
        """Poll the adapter for the latest pairing state while the session is pairing.

        The live pairing thread writes only `pairing_state` (PAIRED, FAILED, ...); the
        matching `setup_status` (READY, FAILED, ...) is derived here. That includes the
        thread's final, non-active state, or a paired session would stay in PAIRING and
        could never be finished (JAN-223).
        """
        self._ensure_not_terminal(session)
        if session.setup_status != models.WhatsAppSetupStatus.PAIRING:
            return session
        snapshot = NeonizeAdapter(account=session.account).poll_pairing_state(session)
        self._apply_pairing_snapshot(session, snapshot)
        self.db.commit()
        self.db.refresh(session)
        return session

    def cancel_setup_session(self, session: models.WhatsAppSetupSession) -> models.WhatsAppSetupSession:
        """Cancel an in-flight setup session."""
        if session.setup_status in TERMINAL_SETUP_STATUSES:
            return session
        NeonizeAdapter(account=session.account).cancel_pairing(session)
        session.setup_status = models.WhatsAppSetupStatus.CANCELLED
        session.pairing_state = models.WhatsAppPairingState.CANCELLED
        session.pairing_message = "Setup was cancelled."
        session.cancelled_at = time_utils.utcnow()
        session.show_fallback_import = True
        self._touch(session)
        self.db.commit()
        self.db.refresh(session)
        return session

    def import_fallback_session(
        self,
        session: models.WhatsAppSetupSession,
        *,
        session_data: str | None,
        session_database_path: str | None,
    ) -> models.WhatsAppSetupSession:
        """Attach fallback session material to a setup session."""
        self._ensure_not_terminal(session)
        artifact = NeonizeAdapter(account=session.account).import_session_artifact(
            session_data=session_data,
            session_database_path=session_database_path,
        )
        self._store_artifact(session, artifact)
        session.setup_status = models.WhatsAppSetupStatus.READY
        session.pairing_state = models.WhatsAppPairingState.IMPORTED
        session.pairing_message = "Fallback session imported. Finish setup to connect this account."
        session.show_fallback_import = True
        session.last_error = None
        self._touch(session)
        self.db.commit()
        self.db.refresh(session)
        return session

    def complete_setup_session(
        self,
        user: models.User,
        session: models.WhatsAppSetupSession,
    ) -> dict[str, Any]:
        """Finalize setup into a durable account and trigger the bounded initial sync."""
        self._ensure_not_terminal(session)
        if time_utils.normalize_timestamp(session.expires_at) <= time_utils.utcnow():
            self._expire_session(session)
            self.db.commit()
            raise ValueError("This WhatsApp setup session has expired.")

        artifact = NeonizeAdapter(account=session.account).export_session_artifact(session)
        if not artifact.is_configured:
            raise ValueError("No Neonize session artifact is configured for this setup session.")

        account_service = WhatsAppService(self.db)
        payload = {
            "display_name": session.display_name,
            "initial_history_days": session.initial_history_days,
            "session_data": artifact.session_data,
            "session_database_path": artifact.session_database_path,
            # Neonize resumes a paired session by reusing the same sqlite path *and*
            # client UUID (see the live-connector feature spec); persist the exact
            # UUID pairing used (`setup-{session.id}`) so the durable worker reopens
            # the same authenticated client instead of an unpaired one.
            "client_uuid": f"setup-{session.id}",
            "is_enabled": True,
        }
        if session.account_id is None:
            # create_account() starts the connector itself for a new enabled account
            # with session material, so no explicit start is needed here.
            account = account_service.create_account(user, payload)
            session.account_id = account.id
        else:
            from janus.workers.whatsapp import start_account_connector, stop_account_connector

            account = account_service.get_account_for_user(session.account_id, user)
            if account is None:
                raise ValueError("WhatsApp account not found")
            was_enabled = account.is_enabled
            account = account_service.update_account(account, payload)
            if was_enabled:
                # update_account only (re)starts the connector on a disabled->enabled
                # transition; an already-enabled account needs an explicit restart to
                # pick up the freshly paired session material. Stop the existing
                # connector first so we never run two live connectors against the
                # same session database concurrently.
                stop_account_connector(account.id)
                start_account_connector(account)

        session.setup_status = models.WhatsAppSetupStatus.COMPLETED
        session.pairing_state = models.WhatsAppPairingState.PAIRED
        session.pairing_message = "WhatsApp account connected. Messages will sync automatically as they arrive."
        session.completed_at = time_utils.utcnow()
        self._touch(session)
        self.db.commit()
        self.db.refresh(session)
        return {
            "account": account_service.serialize_account(account),
            "sync_result": {
                "account_id": account.id,
                "connector_started": True,
                "message": "WhatsApp connector started. Messages will sync automatically as they arrive.",
            },
        }

    def expire_stale_sessions(self, *, user_id: int | None = None) -> int:
        """Mark stale non-terminal setup sessions as expired."""
        now = time_utils.utcnow()
        query = self.db.query(models.WhatsAppSetupSession).filter(
            models.WhatsAppSetupSession.expires_at <= now,
            models.WhatsAppSetupSession.setup_status.notin_(tuple(TERMINAL_SETUP_STATUSES)),
        )
        if user_id is not None:
            query = query.filter(models.WhatsAppSetupSession.user_id == user_id)

        sessions = query.all()
        for session in sessions:
            self._expire_session(session)
        if sessions:
            self.db.commit()
        return len(sessions)

    def serialize_setup_session(self, session: models.WhatsAppSetupSession) -> dict[str, Any]:
        """Return an API-safe setup-session payload."""
        payload = models.to_dict(session)
        payload["session_configured"] = bool(session.session_data or session.session_database_path)
        payload.pop("session_data", None)
        return payload

    def _apply_pairing_capability(
        self,
        session: models.WhatsAppSetupSession,
        capability: NeonizePairingCapability,
    ) -> None:
        """Copy capability flags into the setup session."""
        session.pairing_supported = capability.supported
        session.show_fallback_import = capability.fallback_available or not capability.supported
        if capability.supported:
            if session.setup_status not in TERMINAL_SETUP_STATUSES:
                session.setup_status = models.WhatsAppSetupStatus.CREATED
            if session.pairing_state == models.WhatsAppPairingState.UNSUPPORTED:
                session.pairing_state = models.WhatsAppPairingState.IDLE
            if not session.pairing_message:
                session.pairing_message = capability.message
        else:
            session.setup_status = models.WhatsAppSetupStatus.AWAITING_FALLBACK
            session.pairing_state = models.WhatsAppPairingState.UNSUPPORTED
            session.pairing_message = capability.message
            session.last_error = capability.message

    def _apply_pairing_snapshot(
        self,
        session: models.WhatsAppSetupSession,
        snapshot: NeonizePairingSnapshot,
    ) -> None:
        """Copy a pairing snapshot into the persisted setup session state."""
        session.pairing_state = snapshot.state
        session.pairing_message = snapshot.message
        session.pairing_code = snapshot.pairing_code
        session.pairing_qr_payload = snapshot.pairing_qr_payload
        session.show_fallback_import = snapshot.show_fallback_import
        self._store_artifact(
            session,
            NeonizeSessionArtifact(
                session_data=snapshot.session_data,
                session_database_path=snapshot.session_database_path,
            ),
        )

        if snapshot.state in ACTIVE_PAIRING_STATES:
            session.setup_status = models.WhatsAppSetupStatus.PAIRING
            session.last_error = None
        elif snapshot.state == models.WhatsAppPairingState.PAIRED:
            session.setup_status = models.WhatsAppSetupStatus.READY
            session.last_error = None
        elif snapshot.state == models.WhatsAppPairingState.UNSUPPORTED:
            session.setup_status = models.WhatsAppSetupStatus.AWAITING_FALLBACK
            session.last_error = snapshot.message
        elif snapshot.state == models.WhatsAppPairingState.FAILED:
            session.setup_status = models.WhatsAppSetupStatus.FAILED
            session.last_error = snapshot.message
        elif snapshot.state == models.WhatsAppPairingState.CANCELLED:
            session.setup_status = models.WhatsAppSetupStatus.CANCELLED
            session.cancelled_at = time_utils.utcnow()
            session.last_error = snapshot.message

        self._touch(session)

    def _store_artifact(self, session: models.WhatsAppSetupSession, artifact: NeonizeSessionArtifact) -> None:
        """Persist transient session material without exposing plaintext at rest."""
        if artifact.session_data:
            session.session_data = security_service.encrypt(artifact.session_data)
        if artifact.session_database_path:
            session.session_database_path = artifact.session_database_path

    def _touch(self, session: models.WhatsAppSetupSession) -> None:
        """Refresh the setup-session expiry horizon."""
        session.expires_at = self._next_expiry()

    def _expire_session(self, session: models.WhatsAppSetupSession) -> None:
        """Move one setup session into an expired terminal state."""
        session.setup_status = models.WhatsAppSetupStatus.EXPIRED
        session.pairing_state = models.WhatsAppPairingState.EXPIRED
        session.pairing_message = "This WhatsApp setup session expired. Start a new setup to continue."
        session.show_fallback_import = True
        session.cancelled_at = session.cancelled_at or time_utils.utcnow()

    def _ensure_not_terminal(self, session: models.WhatsAppSetupSession) -> None:
        """Reject mutations against completed/cancelled/expired sessions."""
        if session.setup_status == models.WhatsAppSetupStatus.COMPLETED:
            raise ValueError("This WhatsApp setup session is already completed.")
        if session.setup_status == models.WhatsAppSetupStatus.CANCELLED:
            raise ValueError("This WhatsApp setup session was cancelled.")
        if session.setup_status == models.WhatsAppSetupStatus.EXPIRED:
            raise ValueError("This WhatsApp setup session has expired.")

    def _next_expiry(self):
        """Return the next setup-session expiration timestamp."""
        return time_utils.utcnow() + SETUP_SESSION_TTL
