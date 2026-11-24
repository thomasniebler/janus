"""API router for agent skills and agent-skill attachments.

Legacy per-conversation agent model (compatibility path, JAN-169 cleanup
pending); superseded by /api/character and /api/runtime/triggers for the
plan-driven runtime (JAN-157).
"""

import html
import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from janus import database, models, security, time_utils
from janus.llm_service import LLMService
from janus.schemas import skill as skill_schemas
from janus.services import skill_catalog_service

router = APIRouter(prefix="/api", tags=["skills"])


def _build_skill_payload(
    *,
    name: str,
    description: str,
    license: str | None,
    content: str,
) -> skill_schemas.SkillCreate:
    """Validate and normalize a skill payload before persistence."""
    normalized_name = skill_catalog_service.normalize_skill_name(name)
    if not normalized_name:
        raise HTTPException(status_code=400, detail="Unable to derive a valid skill name")
    try:
        return skill_schemas.SkillCreate.model_validate(
            {
                "name": normalized_name,
                "description": description.strip(),
                "license": license.strip() if license else None,
                "content": content.strip(),
            }
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Invalid skill payload") from exc


def _extract_json_payload(raw: str) -> dict:
    """Extract a JSON object from direct text or fenced markdown output."""
    text = raw.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        last_fence = text.rfind("```")
        if first_newline != -1 and last_fence > first_newline:
            text = text[first_newline + 1 : last_fence].strip()
    return json.loads(text)


def _sanitize_prompt_fragment(value: str | None) -> str:
    """Escape user-provided text before interpolation into LLM prompts."""
    return html.escape((value or "").strip(), quote=False)


@router.get("/skills", response_model=list[skill_schemas.SkillSummary])
def list_skills(
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Returns all skills without content bodies."""
    rows = db.query(models.AgentSkill).order_by(models.AgentSkill.created_at.asc()).all()
    return [skill_schemas.SkillSummary.model_validate(row) for row in rows]


@router.get("/skills/discover", response_model=list[skill_schemas.RemoteSkillSummary])
def discover_skills(
    query: str = "",
    limit: int = 20,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Searches the GitHub-backed community SKILL.md catalog and returns normalized remote skill results."""
    del db, user
    return skill_catalog_service.search_remote_skills(query, limit=limit)


@router.post("/skills/import", response_model=skill_schemas.SkillDetail)
def import_skill(
    body: skill_schemas.SkillImportRequest,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Imports a skill from the GitHub-backed community catalog into local persisted skills."""
    del user
    remote = skill_catalog_service.get_remote_skill(body.remote_id) or {}
    imported_name = body.name if body.name is not None else str(remote.get("name") or body.remote_id)
    imported_description = body.description if body.description is not None else str(remote.get("description") or "")
    imported_license = body.license if body.license is not None else remote.get("license")
    imported_content = body.content if body.content is not None else remote.get("content")
    if not imported_content:
        raise HTTPException(status_code=400, detail="The selected remote skill did not provide importable content")

    payload = _build_skill_payload(
        name=imported_name,
        description=imported_description,
        license=str(imported_license) if imported_license is not None else None,
        content=str(imported_content),
    )
    existing = db.query(models.AgentSkill).filter(models.AgentSkill.name == payload.name).first()
    if existing:
        raise HTTPException(status_code=409, detail="Skill with this name already exists")

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
    return skill


@router.post("/skills/design-assist", response_model=skill_schemas.SkillDesignAssistResponse)
def skill_design_assist(
    body: skill_schemas.SkillDesignAssistRequest,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Returns LLM-powered guidance for creating or refining a skill."""
    del db, user
    llm = LLMService()
    prompt = f"""
You are assisting with writing a reusable AI agent skill.
Return only JSON with keys:
- suggested_name: lowercase-hyphen slug
- suggested_description: concise plain-language purpose
- suggested_content: markdown skill content
- guidance: short explanation of why this shape works
- follow_up_questions: list of 2-4 concise clarifying questions

User goal:
{_sanitize_prompt_fragment(body.goal)}

Current draft name:
{_sanitize_prompt_fragment(body.draft_name)}

Current draft description:
{_sanitize_prompt_fragment(body.draft_description)}

Current draft content:
{_sanitize_prompt_fragment(body.draft_content)}
"""
    try:
        response = llm.llm.invoke(prompt)
        parsed = _extract_json_payload(getattr(response, "content", str(response)))
        suggested_name = skill_catalog_service.normalize_skill_name(
            str(parsed.get("suggested_name") or body.draft_name or "")
        )
        if not suggested_name:
            suggested_name = "new-skill"
        return skill_schemas.SkillDesignAssistResponse.model_validate(
            {
                "suggested_name": suggested_name,
                "suggested_description": str(parsed.get("suggested_description") or body.draft_description or ""),
                "suggested_content": str(parsed.get("suggested_content") or body.draft_content or ""),
                "guidance": str(parsed.get("guidance") or "Refine the scope, constraints, and expected output format."),
                "follow_up_questions": parsed.get("follow_up_questions") or [],
            }
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Unable to generate skill design guidance: {exc}") from exc


@router.get("/skills/{skill_id}", response_model=skill_schemas.SkillDetail)
def get_skill(
    skill_id: int,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Returns a single skill including full content."""
    skill = db.get(models.AgentSkill, skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    return skill


@router.post("/skills", response_model=skill_schemas.SkillDetail)
def create_skill(
    body: skill_schemas.SkillCreate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Creates a new skill."""
    existing = db.query(models.AgentSkill).filter(models.AgentSkill.name == body.name).first()
    if existing:
        raise HTTPException(status_code=409, detail="Skill with this name already exists")

    now = time_utils.utcnow()
    skill = models.AgentSkill(
        name=body.name,
        description=body.description,
        license=body.license,
        content=body.content,
        created_at=now,
        updated_at=now,
    )
    db.add(skill)
    db.commit()
    db.refresh(skill)
    return skill


@router.put("/skills/{skill_id}", response_model=skill_schemas.SkillDetail)
def update_skill(
    skill_id: int,
    body: skill_schemas.SkillUpdate,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Updates a skill."""
    skill = db.get(models.AgentSkill, skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill not found")

    updates = body.model_dump(exclude_unset=True)
    new_name = updates.get("name")
    if new_name and new_name != skill.name:
        existing = db.query(models.AgentSkill).filter(models.AgentSkill.name == new_name).first()
        if existing:
            raise HTTPException(status_code=409, detail="Skill with this name already exists")

    for field, value in updates.items():
        setattr(skill, field, value)
    skill.updated_at = time_utils.utcnow()

    db.commit()
    db.refresh(skill)
    return skill


@router.delete("/skills/{skill_id}", status_code=204)
def delete_skill(
    skill_id: int,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(security.get_current_user),
):
    """Deletes a skill from the library."""
    skill = db.get(models.AgentSkill, skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill not found")

    db.delete(skill)
    db.commit()
