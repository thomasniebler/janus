"""Admin skill-library tools for the single Janus agent."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool, tool
from pydantic import ValidationError

from janus import models, time_utils
from janus.agent_toolboxes.shared import (
    ToolContext,
    log_management_event,
    require_management_access,
    serialize_skill,
    serialize_skill_summary,
)
from janus.schemas import skill as skill_schemas


def build_skill_tools(context: ToolContext, *, skill_catalog: Any) -> list[BaseTool]:  # noqa: C901
    """Build admin-only tools that manage the flat skill library."""
    db = context.db
    user = context.user

    @tool("create_skill")
    def create_skill(name: str, description: str, content: str, license: str | None = None) -> dict[str, Any]:
        """Creates a new persisted reusable skill."""
        action = "create_skill"
        try:
            require_management_access(user)
            normalized_name = skill_catalog.normalize_skill_name(name)
            if not normalized_name:
                raise ValueError("unable to derive a valid skill name from the provided name")
            payload = skill_schemas.SkillCreate.model_validate(
                {"name": normalized_name, "description": description, "license": license, "content": content}
            )
            existing = db.query(models.AgentSkill).filter(models.AgentSkill.name == payload.name).first()
            if existing:
                raise ValueError("skill with this name already exists")
            now = time_utils.utcnow()
            skill = models.AgentSkill(
                name=payload.name,
                description=payload.description,
                license=payload.license,
                content=payload.content,
                created_at=now,
                updated_at=now,
            )
            db.add(skill)
            db.commit()
            db.refresh(skill)
            result = serialize_skill(db, skill)
            log_management_event(
                db,
                event_type=models.EventType.SUCCESS,
                action=action,
                message="skill created",
                details={"user_id": user.id, "skill_id": skill.id, "name": skill.name},
            )
            return result
        except (ValidationError, PermissionError, ValueError) as exc:
            db.rollback()
            log_management_event(
                db,
                event_type=models.EventType.WARNING,
                action=action,
                message="failed",
                details={"user_id": user.id, "error": str(exc)},
            )
            raise ValueError(str(exc)) from exc
        except Exception as exc:
            db.rollback()
            log_management_event(
                db,
                event_type=models.EventType.ERROR,
                action=action,
                message="failed",
                details={"user_id": user.id, "error": "unexpected skill-management failure"},
            )
            raise ValueError("unable to create skill") from exc

    @tool("list_skills")
    def list_skills(limit: int = 100) -> list[dict[str, Any]]:
        """Lists persisted reusable skills."""
        require_management_access(user)
        rows = db.query(models.AgentSkill).order_by(models.AgentSkill.name.asc()).limit(limit).all()
        return [serialize_skill_summary(db, skill) for skill in rows]

    @tool("update_skill")
    def update_skill(
        skill_id: int,
        name: str | None = None,
        description: str | None = None,
        license: str | None = None,
        content: str | None = None,
    ) -> dict[str, Any]:
        """Updates an existing persisted skill."""
        action = "update_skill"
        try:
            require_management_access(user)
            skill = db.get(models.AgentSkill, skill_id)
            if skill is None:
                raise ValueError("skill not found")
            normalized_name: str | None = None
            if name is not None:
                normalized_name = skill_catalog.normalize_skill_name(name)
                if not normalized_name:
                    raise ValueError("unable to derive a valid skill name from the provided name")
            updates = {
                key: value
                for key, value in {
                    "name": normalized_name,
                    "description": description,
                    "license": license,
                    "content": content,
                }.items()
                if value is not None
            }
            if not updates:
                raise ValueError("provide at least one field to update")
            payload = skill_schemas.SkillUpdate.model_validate(updates)
            new_name = payload.name
            if new_name and new_name != skill.name:
                existing = db.query(models.AgentSkill).filter(models.AgentSkill.name == new_name).first()
                if existing:
                    raise ValueError("skill with this name already exists")
            for field, value in payload.model_dump(exclude_unset=True).items():
                setattr(skill, field, value)
            skill.updated_at = time_utils.utcnow()
            db.commit()
            db.refresh(skill)
            result = serialize_skill(db, skill)
            log_management_event(
                db,
                event_type=models.EventType.SUCCESS,
                action=action,
                message="skill updated",
                details={"user_id": user.id, "skill_id": skill.id},
            )
            return result
        except (ValidationError, PermissionError, ValueError) as exc:
            db.rollback()
            log_management_event(
                db,
                event_type=models.EventType.WARNING,
                action=action,
                message="failed",
                details={"user_id": user.id, "skill_id": skill_id, "error": str(exc)},
            )
            raise ValueError(str(exc)) from exc
        except Exception as exc:
            db.rollback()
            log_management_event(
                db,
                event_type=models.EventType.ERROR,
                action=action,
                message="failed",
                details={"user_id": user.id, "skill_id": skill_id, "error": "unexpected skill-management failure"},
            )
            raise ValueError("unable to update skill") from exc

    @tool("search_remote_skills")
    def search_remote_skills(query: str = "", limit: int = 10) -> list[dict[str, Any]]:
        """Searches the GitHub-backed community skill catalog for reusable skills that can be imported."""
        require_management_access(user)
        return skill_catalog.search_remote_skills(query, limit=limit)

    @tool("import_remote_skill")
    def import_remote_skill(
        remote_id: str,
        name: str | None = None,
        description: str | None = None,
        license: str | None = None,
        content: str | None = None,
    ) -> dict[str, Any]:
        """Imports a skill from the GitHub-backed community catalog and persists it locally."""
        action = "import_remote_skill"
        try:
            require_management_access(user)
            remote = skill_catalog.get_remote_skill(remote_id) or {}
            resolved_name = skill_catalog.normalize_skill_name(name or remote.get("name") or remote_id)
            resolved_description = description if description is not None else str(remote.get("description") or "")
            resolved_license = license if license is not None else remote.get("license")
            resolved_content = content if content is not None else remote.get("content")
            if not resolved_name:
                raise ValueError("unable to derive a valid skill name from remote skill")
            if not resolved_content:
                raise ValueError("selected remote skill did not include importable content")

            payload = skill_schemas.SkillCreate.model_validate(
                {
                    "name": resolved_name,
                    "description": resolved_description,
                    "license": str(resolved_license) if resolved_license is not None else None,
                    "content": str(resolved_content),
                }
            )
            existing = db.query(models.AgentSkill).filter(models.AgentSkill.name == payload.name).first()
            if existing:
                raise ValueError("skill with this name already exists")

            now = time_utils.utcnow()
            skill = models.AgentSkill(
                name=payload.name,
                description=payload.description,
                license=payload.license,
                content=payload.content,
                created_at=now,
                updated_at=now,
            )
            db.add(skill)
            db.commit()
            db.refresh(skill)
            result = serialize_skill(db, skill)
            log_management_event(
                db,
                event_type=models.EventType.SUCCESS,
                action=action,
                message="skill imported",
                details={"user_id": user.id, "skill_id": skill.id, "remote_id": remote_id},
            )
            return result
        except (ValidationError, PermissionError, ValueError) as exc:
            db.rollback()
            log_management_event(
                db,
                event_type=models.EventType.WARNING,
                action=action,
                message="failed",
                details={"user_id": user.id, "remote_id": remote_id, "error": str(exc)},
            )
            raise ValueError(str(exc)) from exc
        except Exception as exc:
            db.rollback()
            log_management_event(
                db,
                event_type=models.EventType.ERROR,
                action=action,
                message="failed",
                details={"user_id": user.id, "remote_id": remote_id, "error": "unexpected skill-management failure"},
            )
            raise ValueError("unable to import remote skill") from exc

    return [create_skill, list_skills, update_skill, search_remote_skills, import_remote_skill]
