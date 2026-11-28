"""Per-user "Unattended" and "In chat" switches for tool capabilities (JAN-229, JAN-255, JAN-306).

Each tool-registry entry (a capability such as ``mail.send`` or ``github``) is
either *allowed* or *off* for unattended runs: ad-hoc triggers, saved trigger
definitions (including the built-ins), and delegated child tasks. The default
comes from the registry: entries marked ``confirmation="required"`` are off,
everything else is allowed. A user can override any entry on Settings -> Tools
& Connections; overrides live in ``tool_policies``.

This switch is the whole answer to "may an unattended run use this tool",
except for a few fixed limits that stay in code:

* :data:`LOCKED_UNATTENDED` -- entries no unattended run may use, shown as
  locked: ``triggers.propose`` (JAN-235: drafting automations is chat-only),
  ``ui_actions`` (JAN-251: page actions act on a human's open page),
  ``ui_blocks`` (JAN-252: UI blocks are rendered by the web chat),
  ``images`` (JAN-253: image generation costs money and is shown in chat),
  ``ui_introspection`` (JAN-283: reading a human's open page), and
  ``character.propose`` (JAN-301: proposing character-state changes).
  ``events`` reads only the run owner's rows (JAN-272) and ``triggers``
  enforces its own chain guard (JAN-274), so both follow the switch like any
  other tool, default allowed;
* a delegated child gets only read-only tools, narrowed to what its parent had
  (and never ``delegation`` or ``triggers``, which are not read-only);
* the tool *names* in ``runtime_adapter.TASK_MODE_WITHHELD_TOOL_NAMES``;
* :data:`SWITCH_GOVERNED` -- entries whose unattended allowance is a dedicated
  per-user opt-in instead of a ``tool_policies`` row: ``self_evolution``
  (JAN-320) runs unattended exactly when the owner's self-evolution switch is
  on, and never when it is off, not even for a definition that names it.

:func:`unattended_tool_sets` computes two sets per run. ``offered`` is what the
run may see (the runtime adapter's allowlist); ``confirmed`` is what it may
execute. A tool switched off stays offered: calling it answers ``BLOCKED`` and
the refusal is recorded in the run's result, so the model cannot silently
claim it acted.

**Exception -- user-authored trigger definitions.** When the user writes a
trigger definition and picks a tool for it, that pick allows the tool for that
definition even when it is off. This does not extend to built-in (system)
definitions or to ad-hoc triggers the agent schedules. A definition Janus
drafted itself (JAN-235) gets the same exception only once the user approved
it and only while the approval fingerprint still matches its content
(:func:`janus.trigger_approval.has_valid_approval`); a mismatch is
logged and the run falls back to plain policy.

Chat is human-supervised and is not narrowed by the unattended switch.

**In chat (JAN-306).** A ``confirmation="required"`` entry also has an *In
chat* switch: *Ask every time* (the default; every call goes through the
JAN-254 approval card) or *Off in chat*, which withholds the entry from chat
entirely (:func:`chat_withheld_tool_ids`: chat does not request it, so it is
neither offered nor stubbed). JAN-359 adds *Always allow* (below). Other
entries have no chat switch. Unattended runs ignore it.

**Always allow and blanket modes (JAN-359).** The *In chat* switch has a third
value, *Always allow* (:data:`CHAT_MODE_ALWAYS`): the entry runs in chat
without an approval card (:func:`chat_preconfirmed_tool_ids`). The chat
approval card's "Always allow" button sets it. Two per-user blanket modes
(``tool_policy_modes``) sit above the per-tool switches:

* *Unattended: Allow everything* (:data:`MODE_ALLOW_ALL`) -- every entry an
  unattended run may be offered is also confirmed, whatever its switch says,
  and :data:`ALLOW_ALL_UNLOCKS` (image generation and remote MCP tools) are
  no longer locked. The page-bound and propose entries in
  :data:`LOCKED_UNATTENDED`, the self-evolution switch, and a delegated
  child's read-only fence still apply.
* *In chat: Allow all* -- every requested entry runs in chat without asking;
  an entry switched *Off in chat* stays withheld.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from janus import event_logger, models
from janus.delegation_service import DELEGATED_TASK_MODE
from janus.services.mcp_metadata import is_remote_tool_id
from janus.tool_registry import ToolRegistryEntry
from janus.trigger_approval import has_valid_approval

#: Task modes of agent-driven runs with nobody watching; their tools follow this module.
UNATTENDED_TASK_MODES = frozenset({"trigger", "automation", DELEGATED_TASK_MODE})

#: Registry entries no unattended run may use, whatever the user's switch says, with the reason shown in the UI.
#: ``events`` and ``triggers`` left this set in JAN-272/JAN-274 (owner-scoped log, trigger chain guard); a tool that
#: cannot be made safe for unattended use belongs here rather than behind a default-off switch.
LOCKED_UNATTENDED: dict[str, str] = {
    # JAN-235: drafting automations is chat-only; an unattended run must never create further automations.
    "triggers.propose": "Chat only: an unattended run must never draft further automations.",
    # JAN-251: page actions drive the user's open web page; with nobody watching there is no page.
    "ui_actions": "Chat only: page actions act on the page you have open in the web UI.",
    # JAN-252: UI blocks render in the web chat; an unattended run has no chat to show them in.
    "ui_blocks": "Chat only: UI blocks are shown in your chat replies in the web UI.",
    # JAN-253: each image costs money and sends the prompt to a cloud provider; only when you ask in chat.
    "images": "Chat only: images are generated (and paid for) only when you ask for one in chat.",
    # JAN-283: the outline of your open page must never leave a chat turn you are watching.
    "ui_introspection": "Chat only: Janus reads the page you have open in the web UI only while you chat.",
    # JAN-301: Janus proposes character changes only in a chat you are watching; an unattended run never does.
    "character.propose": "Chat only: Janus proposes character changes only while you chat, and you approve them.",
}


#: Every remote MCP tool (registry id ``mcp.<server>.<tool>``) is locked for unattended runs in the first slice
#: (JAN-343): chat offers them, nothing unattended does. Opening them up is JAN-344.
REMOTE_MCP_LOCK_REASON = "Chat only for now: remote MCP tools and remote agents are not offered to unattended runs."


def locked_reason(tool_id: str) -> str | None:
    """Return why ``tool_id`` is locked for unattended runs (a fixed lock or a remote MCP tool), or ``None``."""
    if tool_id in LOCKED_UNATTENDED:
        return LOCKED_UNATTENDED[tool_id]
    return REMOTE_MCP_LOCK_REASON if is_remote_tool_id(tool_id) else None


#: Fixed locks the *Unattended: Allow everything* mode lifts (JAN-359); remote MCP tools are lifted too.
ALLOW_ALL_UNLOCKS = frozenset({"images"})


def unattended_locked_reason(tool_id: str, *, allow_all: bool) -> str | None:
    """Return :func:`locked_reason` for one unattended run, with the *Allow everything* unlocks applied."""
    if allow_all and (tool_id in ALLOW_ALL_UNLOCKS or is_remote_tool_id(tool_id)):
        return None
    return locked_reason(tool_id)


#: Values of the two blanket modes (JAN-359).
MODE_PER_TOOL = "per_tool"
MODE_ALLOW_ALL = "allow_all"
MODES = frozenset({MODE_PER_TOOL, MODE_ALLOW_ALL})


class ModeError(ValueError):
    """Raised when a blanket mode is set to an unknown value."""


def get_modes(db: Session, user_id: int) -> dict[str, str]:
    """Return the user's blanket ``unattended_mode`` and ``chat_mode`` (both default ``per_tool``)."""
    row = db.scalar(select(models.ToolPolicyMode).where(models.ToolPolicyMode.user_id == user_id))
    if row is None:
        return {"unattended_mode": MODE_PER_TOOL, "chat_mode": MODE_PER_TOOL}
    return {
        "unattended_mode": row.unattended_mode if row.unattended_mode in MODES else MODE_PER_TOOL,
        "chat_mode": row.chat_mode if row.chat_mode in MODES else MODE_PER_TOOL,
    }


def unattended_allow_all(db: Session, user_id: int) -> bool:
    """Return whether the user switched unattended runs to *Allow everything*."""
    return get_modes(db, user_id)["unattended_mode"] == MODE_ALLOW_ALL


def set_modes(
    db: Session, user_id: int, *, unattended_mode: str | None = None, chat_mode: str | None = None
) -> dict[str, str]:
    """Change one or both blanket modes (``None`` leaves a mode unchanged); return the new modes."""
    for value in (unattended_mode, chat_mode):
        if value is not None and value not in MODES:
            raise ModeError(f"Unknown mode: {value!r}.")
    row = db.scalar(select(models.ToolPolicyMode).where(models.ToolPolicyMode.user_id == user_id))
    if row is None:
        row = models.ToolPolicyMode(user_id=user_id, unattended_mode=MODE_PER_TOOL, chat_mode=MODE_PER_TOOL)
        db.add(row)
    if unattended_mode is not None:
        row.unattended_mode = unattended_mode
    if chat_mode is not None:
        row.chat_mode = chat_mode
    row.updated_at = datetime.now(UTC).replace(tzinfo=None)
    db.commit()
    event_logger.log(
        models.EventType.WARNING if MODE_ALLOW_ALL in (unattended_mode, chat_mode) else models.EventType.INFO,
        "ToolPolicy",
        "tool_policy.modes_changed",
        {"user_id": user_id, "unattended_mode": row.unattended_mode, "chat_mode": row.chat_mode},
        user_id=user_id,
    )
    return get_modes(db, user_id)


#: Entries whose unattended switch is a dedicated opt-in (JAN-320), with the reason shown in the UI.
SWITCH_GOVERNED: dict[str, str] = {
    "self_evolution": "Follows the Self-evolution switch (Settings -> Tools & Safety -> Self-evolution).",
}


def _switch_states(db: Session, user_id: int) -> dict[str, bool]:
    # Imported lazily: the service pulls in the GitHub/Linear clients.
    from janus.services.self_evolution_service import enabled_for_user_id

    return {"self_evolution": enabled_for_user_id(db, user_id)}


#: Values of the *In chat* switch of a ``confirmation="required"`` entry (JAN-306).
CHAT_MODE_ASK = "ask"
CHAT_MODE_OFF = "off"
#: *Always allow* (JAN-359): runs in chat without an approval card.
CHAT_MODE_ALWAYS = "always"
CHAT_MODES = frozenset({CHAT_MODE_ASK, CHAT_MODE_OFF, CHAT_MODE_ALWAYS})
#: What :func:`describe_policies` reports for an entry without a chat switch: it runs without asking.
CHAT_MODE_ALLOWED = "allowed"

#: Marks a switch :func:`update_policy` leaves unchanged.
UNCHANGED: object = object()


class LockedToolError(ValueError):
    """Raised when a locked tool's unattended switch is changed."""


class ChatSwitchError(ValueError):
    """Raised when the *In chat* switch is set on an entry that has none, or to an unknown value."""


def delegable_tool_ids(authorized: frozenset[str], entries: Iterable[ToolRegistryEntry]) -> frozenset[str]:
    """Return the tools a delegated child may be offered: the parent's grant, read-only, never locked."""
    return frozenset(
        entry.id
        for entry in entries
        if entry.id in authorized and entry.operation == "read" and locked_reason(entry.id) is None
    )


def default_unattended_allowed(entry: ToolRegistryEntry) -> bool:
    """Return the registry default: everything except ``confirmation="required"`` entries is allowed."""
    return entry.confirmation != "required"


def has_chat_switch(entry: ToolRegistryEntry) -> bool:
    """Return whether the entry has an *In chat* switch: only ``confirmation="required"`` entries do."""
    return entry.confirmation == "required"


def default_chat_mode(entry: ToolRegistryEntry) -> str:
    """Return the chat default: ``required`` entries ask every time, the rest run without asking."""
    return CHAT_MODE_ASK if has_chat_switch(entry) else CHAT_MODE_ALLOWED


def _rows(db: Session, user_id: int) -> list[models.ToolPolicy]:
    return list(db.scalars(select(models.ToolPolicy).where(models.ToolPolicy.user_id == user_id)))


def _overrides(db: Session, user_id: int) -> dict[str, bool]:
    return {
        row.tool_id: bool(row.unattended_allowed) for row in _rows(db, user_id) if row.unattended_allowed is not None
    }


def _chat_overrides(db: Session, user_id: int) -> dict[str, str]:
    return {row.tool_id: row.chat_mode for row in _rows(db, user_id) if row.chat_mode in CHAT_MODES}


def chat_withheld_tool_ids(db: Session, user_id: int, entries: Iterable[ToolRegistryEntry]) -> frozenset[str]:
    """Return the ids of ``required`` entries this user switched *Off in chat* (JAN-306).

    Chat leaves them out of its request, so they are not offered and not
    stubbed. Only ``required`` entries can be withheld; a stored value on any
    other entry is ignored.
    """
    chat = _chat_overrides(db, user_id)
    return frozenset(entry.id for entry in entries if has_chat_switch(entry) and chat.get(entry.id) == CHAT_MODE_OFF)


def chat_preconfirmed_tool_ids(db: Session, user_id: int, entries: Iterable[ToolRegistryEntry]) -> frozenset[str]:
    """Return the ids chat runs without an approval card (JAN-254, JAN-359).

    Entries that are not ``confirmation="required"`` always; ``required``
    entries switched *Always allow*, or every entry while the user's *In chat*
    mode is *Allow all*. Entries switched *Off in chat* are left out (chat does
    not request them anyway).
    """
    entry_list = list(entries)
    chat = _chat_overrides(db, user_id)
    allow_all = get_modes(db, user_id)["chat_mode"] == MODE_ALLOW_ALL
    preconfirmed = set()
    for entry in entry_list:
        if not has_chat_switch(entry):
            preconfirmed.add(entry.id)
        elif chat.get(entry.id) == CHAT_MODE_OFF:
            continue
        elif allow_all or chat.get(entry.id) == CHAT_MODE_ALWAYS:
            preconfirmed.add(entry.id)
    return frozenset(preconfirmed)


def unattended_allowed_tool_ids(db: Session, user_id: int, entries: Iterable[ToolRegistryEntry]) -> frozenset[str]:
    """Return the ids of registry entries the user allows in unattended runs (locked ones never).

    Under *Allow everything* (JAN-359) that is every entry not locked for it,
    except a switch-governed entry whose own switch is off.
    """
    switches = _switch_states(db, user_id)
    if unattended_allow_all(db, user_id):
        return frozenset(
            entry.id
            for entry in entries
            if unattended_locked_reason(entry.id, allow_all=True) is None and switches.get(entry.id, True)
        )
    overrides = {**_overrides(db, user_id), **switches}
    return frozenset(
        entry.id
        for entry in entries
        if locked_reason(entry.id) is None and overrides.get(entry.id, default_unattended_allowed(entry))
    )


def describe_policies(db: Session, user_id: int, entries: Iterable[ToolRegistryEntry]) -> list[dict[str, object]]:
    """Return one policy row per registry entry for the settings UI."""
    # Imported lazily: runtime_adapter imports the registry, whose toolboxes import this module's callers.
    from janus.runtime_adapter import TRIGGER_WITHHELD_TOOL_NAMES_BY_ENTRY

    entries = list(entries)
    overrides = _overrides(db, user_id)
    chat = _chat_overrides(db, user_id)
    switches = _switch_states(db, user_id)
    effective = unattended_allowed_tool_ids(db, user_id, entries)
    chat_preconfirmed = chat_preconfirmed_tool_ids(db, user_id, entries)
    rows = []
    for entry in entries:
        locked = locked_reason(entry.id)
        default = default_unattended_allowed(entry) and locked is None
        if entry.id in SWITCH_GOVERNED:
            # Changed on its own settings card, never here: while that switch is off the row is shown
            # locked (so no definition editor offers it); while on it is allowed, and set_policy refuses.
            allowed, default, overridden = switches[entry.id], False, False
            locked = None if allowed else SWITCH_GOVERNED[entry.id]
        else:
            allowed = locked is None and overrides.get(entry.id, default)
            overridden = locked is None and entry.id in overrides
        chat_default = default_chat_mode(entry)
        chat_mode = chat.get(entry.id, chat_default) if has_chat_switch(entry) else chat_default
        rows.append(
            {
                "tool_id": entry.id,
                "unattended_allowed": allowed,
                "default_unattended_allowed": default,
                "chat_mode": chat_mode,
                "default_chat_mode": chat_default,
                "overridden": overridden or (has_chat_switch(entry) and entry.id in chat),
                "locked_reason": locked,
                # What actually applies once the blanket modes are taken into account (JAN-359).
                "effective_unattended_allowed": entry.id in effective,
                "effective_chat_asks": has_chat_switch(entry)
                and chat_mode != CHAT_MODE_OFF
                and entry.id not in chat_preconfirmed,
                "withheld_unattended_tools": sorted(TRIGGER_WITHHELD_TOOL_NAMES_BY_ENTRY.get(entry.id, ())),
            }
        )
    return rows


def set_policy(db: Session, user_id: int, tool_id: str, allowed: bool | None) -> None:
    """Allow or turn off one capability in unattended runs, or (``None``) reset it to the default."""
    update_policy(db, user_id, tool_id, unattended_allowed=allowed)


def update_policy(
    db: Session,
    user_id: int,
    tool_id: str,
    *,
    unattended_allowed: bool | None | object = UNCHANGED,
    chat_mode: str | None | object = UNCHANGED,
    entry: ToolRegistryEntry | None = None,
) -> None:
    """Change one capability's switches; a switch left :data:`UNCHANGED` keeps its value, ``None`` resets it.

    ``chat_mode`` (``"ask"``/``"off"``) needs the registry ``entry`` and is
    refused (:class:`ChatSwitchError`) unless it is ``confirmation="required"``.
    Changing a locked or switch-governed entry's unattended switch raises
    :class:`LockedToolError`. Both checks run before anything is written. A
    row whose switches are both back to the default is deleted.
    """
    if unattended_allowed is not UNCHANGED and (locked_reason(tool_id) or tool_id in SWITCH_GOVERNED):
        raise LockedToolError(locked_reason(tool_id) or SWITCH_GOVERNED[tool_id])
    if chat_mode is not UNCHANGED and chat_mode is not None:
        if entry is None or not has_chat_switch(entry):
            raise ChatSwitchError("Only tools that ask before every call in chat have an In chat switch.")
        if chat_mode not in CHAT_MODES:
            raise ChatSwitchError(f"Unknown In chat setting: {chat_mode!r}.")
    row = db.scalar(
        select(models.ToolPolicy).where(models.ToolPolicy.user_id == user_id, models.ToolPolicy.tool_id == tool_id)
    )
    if row is None:
        row = models.ToolPolicy(user_id=user_id, tool_id=tool_id, unattended_allowed=None, chat_mode=None)
        db.add(row)
    if unattended_allowed is not UNCHANGED:
        row.unattended_allowed = unattended_allowed
    if chat_mode is not UNCHANGED:
        row.chat_mode = chat_mode
    if row.unattended_allowed is None and row.chat_mode is None:
        if row in db.new:
            db.expunge(row)
        else:
            db.delete(row)
    else:
        row.updated_at = datetime.now(UTC).replace(tzinfo=None)
    db.commit()


def unattended_tool_sets(
    db: Session,
    user_id: int,
    entries: Iterable[ToolRegistryEntry],
    *,
    task_mode: str,
    narrowed_to: frozenset[str] | None = None,
    definition: models.TriggerDefinition | None = None,
) -> tuple[frozenset[str], frozenset[str]]:
    """Return ``(offered, confirmed)`` tool ids for one unattended run.

    ``offered`` is every registry entry except the locked ones, narrowed to
    ``narrowed_to`` (a definition's tools, or a delegated child's parent
    grant) and, for a delegated child, to read-only entries. ``confirmed`` is
    the offered entries the user allows unattended, plus -- for a
    user-authored ``definition`` -- the tools the user picked for it.
    Connection state is not checked here; the runtime adapter denies
    disconnected tools itself. A mode outside :data:`UNATTENDED_TASK_MODES`
    gets nothing.
    """
    if task_mode not in UNATTENDED_TASK_MODES:
        return frozenset(), frozenset()
    entry_list = list(entries)
    allow_all = unattended_allow_all(db, user_id)
    offered = {entry.id for entry in entry_list if unattended_locked_reason(entry.id, allow_all=allow_all) is None}
    if narrowed_to is not None:
        offered &= narrowed_to
    if task_mode == DELEGATED_TASK_MODE:
        offered &= {entry.id for entry in entry_list if entry.operation == "read"}
    confirmed = set(unattended_allowed_tool_ids(db, user_id, entry_list))
    if definition is not None and definition.origin == models.TriggerDefinitionOrigin.USER.value:
        confirmed.update(definition.tool_ids or ())
    elif definition is not None and definition.origin == models.TriggerDefinitionOrigin.AGENT.value:
        if has_valid_approval(definition):
            confirmed.update(definition.tool_ids or ())
        elif definition.proposal_status == models.TriggerProposalStatus.APPROVED.value:
            event_logger.log(
                models.EventType.WARNING,
                "TriggerProposals",
                "trigger_proposal.fingerprint_mismatch",
                {"definition_id": definition.id, "user_id": definition.user_id},
            )
    # A definition naming a switch-governed entry never overrides that switch.
    confirmed -= {tool_id for tool_id, on in _switch_states(db, user_id).items() if not on}
    return frozenset(offered), frozenset(offered & confirmed)


__all__ = [
    "ALLOW_ALL_UNLOCKS",
    "CHAT_MODES",
    "CHAT_MODE_ALWAYS",
    "MODES",
    "MODE_ALLOW_ALL",
    "MODE_PER_TOOL",
    "ModeError",
    "chat_preconfirmed_tool_ids",
    "get_modes",
    "set_modes",
    "unattended_allow_all",
    "unattended_locked_reason",
    "CHAT_MODE_ALLOWED",
    "CHAT_MODE_ASK",
    "CHAT_MODE_OFF",
    "LOCKED_UNATTENDED",
    "REMOTE_MCP_LOCK_REASON",
    "SWITCH_GOVERNED",
    "UNATTENDED_TASK_MODES",
    "UNCHANGED",
    "ChatSwitchError",
    "LockedToolError",
    "chat_withheld_tool_ids",
    "default_chat_mode",
    "default_unattended_allowed",
    "delegable_tool_ids",
    "describe_policies",
    "has_chat_switch",
    "locked_reason",
    "set_policy",
    "update_policy",
    "unattended_allowed_tool_ids",
    "unattended_tool_sets",
]
