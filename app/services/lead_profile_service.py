"""Service layer for managing structured lead profiles."""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any, Dict, Optional

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LeadProfile, User
from app.repositories.lead_profile_repository import LeadProfileRepository
from app.services.user_service import UserService


DEFAULT_PROFILE_TEMPLATE: Dict[str, Any] = {
    "name": None,
    "interest_level": None,
    "financial_goals": [],
    "investment_experience": None,
    "objections": [],
    "emotional_type": None,
    "consultation_readiness": None,
    "tone_preference": None,
    "communication_style": None,
    "entry_context": None,
    "vector": None,
    "goal_picture": {
        "goal": None,
        "six_months_signs": None,
        "relief": None,
    },
    "diagnostics": {
        "facts": None,
        "implications": None,
        "causes": None,
    },
    "priority_scale": {
        "current_level": None,
        "target_level": None,
    },
    "notable_quotes": [],
    "personal_value_drivers": [],
}

STAGE_SEQUENCE = [
    "opening",
    "frame",
    "goal",
    "diagnostics",
    "gap",
    "solution",
    "proof",
    "objections",
    "scarcity",
    "closing",
]


class LeadProfileService:
    """Business service for manipulating lead profiles."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repository = LeadProfileRepository(session)
        self.user_service = UserService(session)
        self.logger = structlog.get_logger(__name__)

    async def get_or_create(self, user: User) -> LeadProfile:
        """Return existing profile or create a fresh one."""
        profile = await self.repository.get_by_user_id(user.id)
        if profile:
            if not profile.profile_data:
                profile.profile_data = deepcopy(DEFAULT_PROFILE_TEMPLATE)
                profile = await self.repository.save(profile)
            return profile

        profile = await self.repository.create(user.id)
        profile.profile_data = deepcopy(DEFAULT_PROFILE_TEMPLATE)
        profile.current_stage = STAGE_SEQUENCE[0]
        return await self.repository.save(profile)

    async def apply_agent_payload(
        self,
        *,
        user: User,
        profile: LeadProfile,
        payload: Dict[str, Any],
    ) -> LeadProfile:
        """Merge structured payload from the dialog agent into the profile."""
        if not payload:
            return await self.repository.save(profile)

        profile_updates: Dict[str, Any] = payload.get("lead_profile_updates") or {}
        scenario = payload.get("scenario")
        next_stage = payload.get("next_stage")
        summary = payload.get("lead_summary") or payload.get("summary")
        readiness_score = payload.get("readiness_score")
        client_label = payload.get("client_label")
        handoff_trigger = payload.get("handoff_trigger")
        handoff_ready = payload.get("handoff_ready")
        agent_notes = payload.get("agent_notes")

        if profile_updates:
            profile.profile_data = self._merge_profile_data(profile.profile_data, profile_updates)

        if scenario:
            profile.scenario = scenario

        profile.current_stage = self._resolve_next_stage(profile.current_stage, next_stage)

        if summary:
            profile.summary_text = summary

        if isinstance(readiness_score, (int, float)):
            clamped = max(0, min(int(round(readiness_score)), 100))
            profile.readiness_score = clamped
            await self._sync_user_lead_score(user, clamped)

        if client_label:
            profile.client_label = client_label.strip()

        if handoff_trigger:
            profile.handoff_trigger = handoff_trigger

        if isinstance(handoff_ready, bool):
            profile.handoff_ready = handoff_ready

        if agent_notes:
            profile.last_agent_notes = agent_notes

        self.logger.debug(
            "lead_profile_payload_applied",
            user_id=user.id,
            stage=profile.current_stage,
            readiness=profile.readiness_score,
            scenario=profile.scenario,
        )

        return await self.repository.save(profile)

    def _merge_profile_data(self, current: Optional[Dict[str, Any]], updates: Dict[str, Any]) -> Dict[str, Any]:
        """Deep-merge profile data structure."""
        base = deepcopy(DEFAULT_PROFILE_TEMPLATE) if not current else deepcopy(current)

        for key, value in updates.items():
            if value in (None, "", []):
                continue
            if key not in base:
                base[key] = value
                continue

            if isinstance(base[key], dict) and isinstance(value, dict):
                base[key] = self._merge_profile_data(base[key], value)
            elif isinstance(base[key], list) and isinstance(value, list):
                existing = set(base[key])
                merged = base[key][:]
                for item in value:
                    if item not in existing:
                        merged.append(item)
                        existing.add(item)
                base[key] = merged
            else:
                base[key] = value

        return base

    def _resolve_next_stage(self, current_stage: Optional[str], requested_stage: Optional[str]) -> str:
        """Resolve the new stage making sure we do not skip multiple steps."""
        if not current_stage:
            current_stage = STAGE_SEQUENCE[0]
        if not requested_stage:
            return current_stage

        requested_stage = requested_stage.lower()
        if requested_stage not in STAGE_SEQUENCE:
            return current_stage

        current_index = STAGE_SEQUENCE.index(current_stage)
        requested_index = STAGE_SEQUENCE.index(requested_stage)

        if requested_index < current_index:
            return STAGE_SEQUENCE[current_index]

        if requested_index <= current_index + 1:
            return STAGE_SEQUENCE[requested_index]

        # Move only one step forward when agent tries to skip
        next_index = min(current_index + 1, len(STAGE_SEQUENCE) - 1)
        return STAGE_SEQUENCE[next_index]

    async def _sync_user_lead_score(self, user: User, readiness_score: int) -> None:
        """Translate readiness score to legacy lead_score and update user segment."""
        scaled_score = min(15, max(0, math.ceil(readiness_score / 7)))
        await self.user_service.update_user_segment(user, scaled_score)
