"""Persistence service for user-owned character constitutions."""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from janus import models
from janus.character import CharacterConstitution, CharacterTrait
from janus.schemas.character import CharacterProfilePayload

DEFAULT_CHARACTER = CharacterProfilePayload(
    name="Janus",
    identity="A thoughtful local assistant that helps the user make progress while remaining honest about uncertainty.",
    core_values=["Be useful", "Be honest", "Respect user agency"],
    non_negotiables=["Do not invent facts", "Do not bypass authorization", "Protect private data"],
    traits=[],
)


#: Built-in rules for the post-turn memory suggester, used when the user has not
#: written their own on the Character page (JAN-230). The JSON output contract
#: stays in ``llm_service.suggest_memory_fact``; only these rules are editable.
DEFAULT_MEMORY_INSTRUCTIONS = (
    "- Memory exists so a future conversation can rely on what was learned here without the user repeating it.\n"
    "- Store a fact when the user asks to remember something, or when the turn reveals something clearly worth\n"
    "  remembering.\n"
    "- Leave out what can be looked up again (today's mail, calendar, tasks) and what only matters for this turn.\n"
    "- Keep the summary short and concrete; put the user's exact words for anything precise (names, numbers,\n"
    "  terms, dates) in the detail."
)


def constitution_from_payload(
    user_id: int, payload: CharacterProfilePayload, *, version: int = 1
) -> CharacterConstitution:
    """Build a runtime constitution from (possibly unsaved) editable fields."""
    return CharacterConstitution(
        constitution_id=f"user-{user_id}-character",
        version=version,
        name=payload.name,
        identity=payload.identity,
        core_values=tuple(payload.core_values),
        non_negotiables=tuple(payload.non_negotiables),
        traits=tuple(
            CharacterTrait(
                name=trait.name, baseline=trait.baseline, max_delta=trait.max_delta, description=trait.description
            )
            for trait in payload.traits
        ),
        additional_instructions=payload.additional_instructions or "",
    )


def get_memory_instructions(db: Session, user_id: int) -> str:
    """Return the user's memory-suggestion rules, or the built-in default."""
    profile = get_character_profile(db, user_id)
    custom = (profile.memory_instructions or "").strip() if profile is not None else ""
    return custom or DEFAULT_MEMORY_INSTRUCTIONS


def get_character_profile(db: Session, user_id: int) -> models.CharacterProfile | None:
    """Return the persisted profile owned by the user, if one exists."""
    return db.query(models.CharacterProfile).filter(models.CharacterProfile.user_id == user_id).first()


def get_character_constitution(db: Session, user_id: int) -> CharacterConstitution:
    """Return the user's constitution as the runtime contract, falling back to the safe default.

    This is the single source of Janus identity/system-prompt content for
    chat, mail-ingestion guidance, and briefing generation (JAN-201). It
    mirrors what ``GET /api/character`` shows the user, so what they edit in
    Settings is exactly what the runtime renders.
    """
    profile = get_character_profile(db, user_id)
    if profile is None:
        return CharacterConstitution(
            constitution_id=f"user-{user_id}-character",
            version=1,
            name=DEFAULT_CHARACTER.name,
            identity=DEFAULT_CHARACTER.identity,
            core_values=tuple(DEFAULT_CHARACTER.core_values),
            non_negotiables=tuple(DEFAULT_CHARACTER.non_negotiables),
            traits=(),
        )
    return CharacterConstitution(
        constitution_id=profile.constitution_id,
        version=profile.version,
        name=profile.name,
        identity=profile.identity,
        core_values=tuple(profile.core_values or ()),
        non_negotiables=tuple(profile.non_negotiables or ()),
        traits=tuple(
            CharacterTrait(
                name=trait["name"],
                baseline=trait["baseline"],
                max_delta=trait.get("max_delta", 0.1),
                description=trait.get("description", ""),
            )
            for trait in profile.traits or ()
        ),
        additional_instructions=profile.additional_instructions or "",
    )


def save_character_profile(db: Session, user_id: int, payload: CharacterProfilePayload) -> models.CharacterProfile:
    """Create or replace the user's constitution with a new version."""
    now = datetime.now(UTC)
    profile = get_character_profile(db, user_id)
    if profile is None:
        profile = models.CharacterProfile(
            user_id=user_id,
            constitution_id=f"user-{user_id}-character",
            version=1,
            created_at=now,
        )
        db.add(profile)
    else:
        profile.version += 1

    profile.name = payload.name
    profile.identity = payload.identity
    profile.core_values = list(payload.core_values)
    profile.non_negotiables = list(payload.non_negotiables)
    profile.traits = [trait.model_dump() for trait in payload.traits]
    profile.additional_instructions = payload.additional_instructions
    profile.memory_instructions = payload.memory_instructions
    profile.updated_at = now
    db.commit()
    db.refresh(profile)
    return profile
