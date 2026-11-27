"""Event types the Janus connector registers handlers for."""

from __future__ import annotations

from types import SimpleNamespace


class ConnectedEv(SimpleNamespace):
    """The client is connected and logged in."""


class DisconnectedEv(SimpleNamespace):
    """The connection dropped."""


class LoggedOutEv(SimpleNamespace):
    """The linked device was logged out on the phone."""


class ConnectFailureEv(SimpleNamespace):
    """WhatsApp refused the connection (`Reason`, `Message`)."""


class MessageEv(SimpleNamespace):
    """One live message (`Info`, `Message`)."""


class HistorySyncEv(SimpleNamespace):
    """The one-time post-pairing backlog push (`Data.conversations`)."""


class PairStatusEv(SimpleNamespace):
    """Result of the phone accepting (or rejecting) the QR code (`Status`, `Error`)."""

    SUCCESS = 1
    ERROR = 2
