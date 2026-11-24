"""Notification service for desktop alerts."""

import logging
import os
import subprocess

from janus import models
from janus.config import settings

logger = logging.getLogger(__name__)


class NotificationService:
    """Service for sending desktop notifications."""

    desktop_integrations_enabled = not settings.server_mode

    @classmethod
    def configure(cls, *, enabled: bool) -> None:
        """Enables or disables desktop-only notification integrations."""
        cls.desktop_integrations_enabled = enabled

    @staticmethod
    def notify(email: models.EmailRecord):
        """Sends a desktop notification for an important email.

        Args:
            email (EmailRecord): The email record to notify about.
        """
        if not NotificationService.desktop_integrations_enabled:
            logger.info("Skipping desktop notification for %s because server mode is enabled.", email.message_id)
            return

        title = f"Janus: Important Mail from {email.sender}"
        body = f"Subject: {email.subject}\n\n{email.snippet}..."

        # Simple cross-platform check (mostly Linux-focused for now)
        try:
            if os.name == "posix":  # Linux/MacOS
                subprocess.run(["notify-send", title, body], check=False)
                logger.info(f"Notification sent for email {email.message_id}")
            else:
                logger.warning(f"Notifications not yet implemented for OS: {os.name}")
        except FileNotFoundError:
            logger.warning("notify-send not found. Install libnotify-bin on Linux.")
        except Exception as e:
            logger.error(f"Failed to send notification: {e}")

    @staticmethod
    def notify_digest(summary: str):
        """Sends a desktop notification for a scheduled daily digest summary."""
        if not NotificationService.desktop_integrations_enabled:
            logger.info("Skipping desktop digest notification because server mode is enabled.")
            return

        title = "Janus: Daily Digest"
        body = summary

        try:
            if os.name == "posix":
                subprocess.run(["notify-send", title, body], check=False)
                logger.info("Daily digest notification sent.")
            else:
                logger.warning("Digest notifications not yet implemented for OS: %s", os.name)
        except FileNotFoundError:
            logger.warning("notify-send not found. Install libnotify-bin on Linux.")
        except Exception as e:
            logger.error("Failed to send digest notification: %s", e)

    @staticmethod
    def notify_alert(title: str, body: str):
        """Sends a desktop notification for an alert (e.g. a failed self-evolution self-check, JAN-321)."""
        if not NotificationService.desktop_integrations_enabled:
            logger.info("Skipping desktop alert %r because server mode is enabled.", title)
            return
        try:
            if os.name == "posix":
                subprocess.run(["notify-send", "--urgency=critical", title, body], check=False)
            else:
                logger.warning("Alert notifications not yet implemented for OS: %s", os.name)
        except FileNotFoundError:
            logger.warning("notify-send not found. Install libnotify-bin on Linux.")
        except Exception as e:
            logger.error("Failed to send alert notification: %s", e)

    @staticmethod
    def notify_conversation(title: str):
        """Sends a desktop notification that Janus started a new conversation (JAN-319)."""
        if not NotificationService.desktop_integrations_enabled:
            logger.info("Skipping desktop conversation notification because server mode is enabled.")
            return
        try:
            if os.name == "posix":
                subprocess.run(["notify-send", "Janus started a conversation", title], check=False)
            else:
                logger.warning("Conversation notifications not yet implemented for OS: %s", os.name)
        except FileNotFoundError:
            logger.warning("notify-send not found. Install libnotify-bin on Linux.")
        except Exception as e:
            logger.error("Failed to send conversation notification: %s", e)
