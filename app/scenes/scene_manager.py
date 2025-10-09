"""Scene manager for coordinating conversation scenarios."""

from typing import Any, Dict, List, Mapping, Optional, Type
from dataclasses import dataclass, field
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import structlog
from sqlalchemy.ext.asyncio import AsyncSession
try:
    from redis.asyncio import Redis
except ImportError:
    Redis = None

from app.models import User, UserSegment, FunnelStage
from .action_registry import ActionContext, ActionOutcome, ActionRegistry
from .base_scene import BaseScene, SceneState, SceneResponse
from .config_loader import (
    DEFAULT_SCENARIO_CONFIG_PATH,
    ScenarioConfig,
    ScenarioConfigError,
    load_scenario_config,
)


@dataclass
class SceneSession:
    """User's scene session data."""
    current_scene: str
    scene_state: SceneState = field(default_factory=SceneState)
    session_start: str = ""
    last_interaction: str = ""
    current_state: Optional[str] = None


class SceneManager:
    """Manager for conversation scenarios."""
    
    def __init__(
        self,
        session: AsyncSession,
        redis: Optional[Redis] = None,
        *,
        config_path: Optional[str] = None,
        action_registry: Optional[ActionRegistry] = None,
    ):
        self.session = session
        self.redis = redis
        self.logger = structlog.get_logger()

        self._config_path = Path(config_path) if config_path else DEFAULT_SCENARIO_CONFIG_PATH
        self.action_registry = action_registry or ActionRegistry.with_default_actions()
        self.scenario_config: Optional[ScenarioConfig] = None
        self._config_enabled: bool = False
        self._config_default_state: Optional[str] = None

        self._load_config()

        # Scene registry
        self.scenes: Dict[str, Type[BaseScene]] = {}
        self._register_default_scenes()

        # Scene selection rules (legacy path)
        self.scene_rules = {
            # Cold segment (0-4 points) -> Newbie scene
            (UserSegment.COLD, lambda score: score <= 4): "newbie",

            # Warm segment (5-9 points) -> Trader scene
            (UserSegment.WARM, lambda score: 5 <= score <= 9): "trader",

            # Hot segment (10+ points) -> Investor scene
            (UserSegment.HOT, lambda score: score >= 10): "investor",

            # Special cases
            (None, lambda score: score > 13): "skeptic",  # Очень высокий балл без сегмента
        }

    @property
    def config_enabled(self) -> bool:
        """Return True when YAML configuration is active."""
        return self._config_enabled

    def reload_config(self) -> None:
        """Reload scenario configuration from disk."""
        self._load_config()

    def _load_config(self) -> None:
        try:
            config = load_scenario_config(self._config_path)
        except FileNotFoundError:
            self.logger.warning(
                "Scenario config not found, falling back to legacy scenes",
                path=str(self._config_path),
            )
            self.scenario_config = None
            self._config_enabled = False
            self._config_default_state = None
            return
        except ScenarioConfigError as exc:
            self.logger.error(
                "Scenario config invalid",
                path=str(self._config_path),
                error=str(exc),
            )
            self.scenario_config = None
            self._config_enabled = False
            self._config_default_state = None
            return

        self.scenario_config = config
        self._config_enabled = True
        if config.metadata.get("default_state"):
            self._config_default_state = str(config.metadata["default_state"])
        elif "START" in config.states:
            self._config_default_state = "START"
        else:
            self._config_default_state = config.state_names[0]

    def _get_default_state(self) -> Optional[str]:
        if not self._config_enabled or not self.scenario_config:
            return None
        return self._config_default_state
    
    def _register_default_scenes(self):
        """Register all default scene classes."""
        from .newbie_scene import NewbieScene
        from .trader_scene import TraderScene
        from .investor_scene import InvestorScene
        from .skeptic_scene import SkepticScene
        from .strategy_scene import StrategyScene
        
        self.register_scene("newbie", NewbieScene)
        self.register_scene("trader", TraderScene)
        self.register_scene("investor", InvestorScene)
        self.register_scene("skeptic", SkepticScene)
        self.register_scene("strategy", StrategyScene)
    
    def register_scene(self, scene_name: str, scene_class: Type[BaseScene]):
        """Register a scene class."""
        self.scenes[scene_name] = scene_class
        self.logger.debug("Scene registered", scene_name=scene_name)

    async def process_trigger(
        self,
        user: User,
        trigger: str,
        *,
        chat_id: Optional[int] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> SceneResponse:
        """Process trigger according to YAML configuration."""
        if not self._config_enabled or not self.scenario_config:
            self.logger.warning(
                "Scenario config disabled, falling back to legacy handler",
                trigger=trigger,
            )
            return self._create_placeholder_response()

        session = await self._get_scene_session(user)
        state_name = session.current_state or self._get_default_state() or session.current_scene
        if not state_name:
            state_name = self._get_default_state() or "START"

        target_state = self._resolve_transition(state_name, trigger, payload)
        if not target_state:
            self.logger.info(
                "No transition for trigger",
                trigger=trigger,
                state=state_name,
                user_id=user.id,
            )
            return SceneResponse(
                message_text="🤔 Пока не нашёл подходящий ответ. Попробуйте другой вариант.",
                buttons=[],
            )

        session.current_state = target_state
        session.last_interaction = datetime.now(timezone.utc).isoformat()
        if not session.session_start:
            session.session_start = session.last_interaction

        response = await self._execute_state_entry(
            user=user,
            session=session,
            state_name=target_state,
            trigger=trigger,
            chat_id=chat_id,
            payload=payload or {},
        )

        await self._save_scene_session(user, session)
        return response

    async def process_user_message(
        self, 
        user: User, 
        message_text: str
    ) -> SceneResponse:
        """Process user message through appropriate scene."""
        try:
            # Get or create scene session
            scene_session = await self._get_scene_session(user)
            
            # Determine current scene
            scene_name = await self._determine_scene(user, scene_session)
            
            # Update scene if changed
            if scene_name != scene_session.current_scene:
                await self._transition_scene(user, scene_session, scene_name)
            
            # Get scene instance
            scene = self._get_scene_instance(scene_name)
            
            # Process message
            response = await scene.process_message(
                user, message_text, scene_session.scene_state
            )
            
            # Handle scene transitions
            if response.next_scene:
                await self._transition_scene(user, scene_session, response.next_scene)
            
            # Save session state
            await self._save_scene_session(user, scene_session)
            
            # Log scene interaction
            self.logger.info(
                "Scene interaction processed",
                user_id=user.id,
                scene=scene_name,
                action=response.log_event.get("action") if response.log_event else None,
                escalate=response.escalate
            )
            
            return response
            
        except Exception as e:
            self.logger.error(
                "Scene manager error",
                user_id=user.id,
                error=str(e),
                exc_info=True
            )
            return self._create_error_response()
    
    def _resolve_transition(
        self,
        state_name: str,
        trigger: str,
        payload: Optional[Mapping[str, Any]] = None,
    ) -> Optional[str]:
        if not self.scenario_config:
            return None
        try:
            state_config = self.scenario_config.get_state(state_name)
        except ScenarioConfigError:
            self.logger.warning(
                "Unknown state referenced",
                state=state_name,
            )
            return None
        for transition in state_config.transitions:
            if transition.trigger == trigger:
                return transition.target
        return None

    async def _execute_state_entry(
        self,
        user: User,
        session: SceneSession,
        state_name: str,
        *,
        trigger: str,
        chat_id: Optional[int],
        payload: Mapping[str, Any],
    ) -> SceneResponse:
        if not self.scenario_config:
            return self._create_placeholder_response()
        try:
            state_config = self.scenario_config.get_state(state_name)
        except ScenarioConfigError:
            self.logger.error(
                "Attempted to enter unknown state",
                state=state_name,
                user_id=user.id,
            )
            return self._create_placeholder_response()

        context = ActionContext(
            session=self.session,
            user=user,
            bot=None,
            chat_id=chat_id,
            extras={"payload": dict(payload), "trigger": trigger},
        )
        suppress_fallback = bool(state_config.get_extra("suppress_fallback", False))
        message_text: Optional[str] = None
        raw_buttons: List[Dict[str, str]] = []
        accumulated: Dict[str, Any] = {}

        for step in state_config.entry_steps:
            outcome = await self.action_registry.execute(step.action, context, step.params)
            if outcome is None:
                continue
            if outcome.message_text:
                message_text = outcome.message_text
            if outcome.buttons:
                raw_buttons = outcome.buttons
            if outcome.data:
                accumulated.update(outcome.data)

        if not message_text and not suppress_fallback:
            message_text = self._default_state_message(state_name)

        buttons: List[Dict[str, str]] = []
        for button in raw_buttons:
            text_value = button.get("text") if isinstance(button, Mapping) else None
            callback_value = None
            if isinstance(button, Mapping):
                callback_value = button.get("callback_data") or button.get("callback")
            buttons.append({
                "text": str(text_value or ""),
                "callback_data": str(callback_value or ""),
            })

        log_event = None
        if "logged_event" in accumulated:
            log_event = {
                "event": accumulated["logged_event"],
                "state": state_name,
                "trigger": trigger,
            }

        return SceneResponse(
            message_text=message_text,
            buttons=buttons,
            log_event=log_event,
        )

    def _default_state_message(self, state_name: str) -> str:
        return f"Этап {state_name}: функция в разработке."

    def _create_placeholder_response(self) -> SceneResponse:
        return SceneResponse(
            message_text="Пока не готов ответить, но мы уже работаем над сценарием.",
            buttons=[
                {"text": "👤 Менеджер", "callback_data": "manager:request"}
            ],
        )

    async def _determine_scene(self, user: User, session: SceneSession) -> str:
        """Determine appropriate scene for user."""
        
        # If user is in strategy selection, keep them there
        if session.current_scene == "strategy":
            return "strategy"
        
        # Check scene selection rules
        for (segment, score_check), scene_name in self.scene_rules.items():
            if segment is None or user.segment == segment:
                if score_check(user.lead_score):
                    return scene_name
        
        # Default fallback based on funnel stage
        if user.funnel_stage in [FunnelStage.NEW, FunnelStage.WELCOMED]:
            return "strategy"  # Start with strategy selection
        
        # Default to newbie for unknown cases
        return "newbie"
    
    async def _transition_scene(
        self, 
        user: User, 
        session: SceneSession, 
        new_scene: str
    ):
        """Transition user to new scene."""
        old_scene = session.current_scene
        
        # Log transition
        self.logger.info(
            "Scene transition",
            user_id=user.id,
            from_scene=old_scene,
            to_scene=new_scene
        )
        
        # Update session
        session.current_scene = new_scene
        session.scene_state = SceneState()  # Reset state for new scene
        
        # Log event for analytics
        await self._log_scene_transition(user, old_scene, new_scene)
    
    def _get_scene_instance(self, scene_name: str) -> BaseScene:
        """Get scene instance by name."""
        scene_class = self.scenes.get(scene_name)
        if not scene_class:
            # Fallback to a default scene if available
            if "newbie" in self.scenes:
                self.logger.warning(
                    "Scene not found, falling back to newbie",
                    requested_scene=scene_name
                )
                scene_class = self.scenes["newbie"]
            else:
                raise ValueError(f"Unknown scene: {scene_name} and no fallback available")
        
        return scene_class(self.session)
    
    async def _get_scene_session(self, user: User) -> SceneSession:
        """Get user's scene session from cache or create new."""
        cache_key = f"scene_session:{user.id}"
        
        if self.redis:
            try:
                session_data = await self.redis.get(cache_key)
                if session_data:
                    data = json.loads(session_data)
                    return SceneSession(
                        current_scene=data.get("current_scene", ""),
                        scene_state=SceneState(**data.get("scene_state", {})),
                        session_start=data.get("session_start", ""),
                        last_interaction=data.get("last_interaction", ""),
                        current_state=data.get("current_state"),
                    )
            except Exception as e:
                self.logger.warning(
                    "Failed to load scene session from cache",
                    user_id=user.id,
                    error=str(e)
                )
        
        # Create new session
        legacy_session = SceneSession(current_scene="")
        default_scene = await self._determine_scene(user, legacy_session)
        session = SceneSession(current_scene=default_scene)
        session.current_state = self._get_default_state()
        session.session_start = datetime.now(timezone.utc).isoformat()
        session.last_interaction = session.session_start
        return session
    
    async def _save_scene_session(self, user: User, session: SceneSession):
        """Save user's scene session to cache."""
        if not self.redis:
            return
        
        cache_key = f"scene_session:{user.id}"
        
        try:
            session_data = {
                "current_scene": session.current_scene,
                "current_state": session.current_state,
                "scene_state": {
                    "current_step": session.scene_state.current_step,
                    "attempts_count": session.scene_state.attempts_count,
                    "confidence_history": session.scene_state.confidence_history,
                    "context_data": session.scene_state.context_data,
                    "last_action": session.scene_state.last_action,
                    "escalation_triggered": session.scene_state.escalation_triggered,
                },
                "session_start": session.session_start,
                "last_interaction": session.last_interaction,
            }
            
            await self.redis.setex(
                cache_key,
                3600,  # 1 hour expiry
                json.dumps(session_data)
            )
            
        except Exception as e:
            self.logger.warning(
                "Failed to save scene session to cache",
                user_id=user.id,
                error=str(e)
            )
    
    async def _log_scene_transition(self, user: User, old_scene: str, new_scene: str):
        """Log scene transition for analytics."""
        from app.models import Event
        
        event = Event(
            user_id=user.id,
            type="scene_transition",
            payload={
                "from_scene": old_scene,
                "to_scene": new_scene,
                "user_segment": user.segment,
                "lead_score": user.lead_score,
                "funnel_stage": user.funnel_stage
            }
        )
        
        self.session.add(event)
        await self.session.flush()
    
    def _create_error_response(self) -> SceneResponse:
        """Create error response when scene processing fails."""
        return SceneResponse(
            message_text="😔 Произошла ошибка. Попробуйте ещё раз или обратитесь к менеджеру.",
            buttons=[
                {"text": "👤 Менеджер", "callback_data": "manager:request"}
            ],
            escalate=True
        )
    
    async def force_scene_transition(self, user: User, scene_name: str) -> bool:
        """Force user transition to specific scene (admin function)."""
        try:
            if scene_name not in self.scenes:
                return False
            
            session = await self._get_scene_session(user)
            await self._transition_scene(user, session, scene_name)
            await self._save_scene_session(user, session)
            
            return True
            
        except Exception as e:
            self.logger.error(
                "Failed to force scene transition",
                user_id=user.id,
                scene=scene_name,
                error=str(e)
            )
            return False
    
    async def get_scene_analytics(self, days: int = 7) -> Dict[str, Any]:
        """Get scene usage analytics."""
        from app.models import Event
        from sqlalchemy import select, func
        start_date = datetime.now(timezone.utc) - timedelta(days=days)
        
        # Scene transition stats
        stmt = select(
            Event.payload['to_scene'].astext.label('scene'),
            func.count().label('transitions')
        ).where(
            Event.type == 'scene_transition',
            Event.created_at >= start_date
        ).group_by(Event.payload['to_scene'].astext)
        
        result = await self.session.execute(stmt)
        transition_stats = {row.scene: row.transitions for row in result}
        
        # Scene interaction stats
        interaction_stmt = select(
            func.substr(Event.type, 7, func.length(Event.type) - 17).label('scene'),
            func.count().label('interactions'),
            func.avg(Event.payload['confidence'].astext.cast(float)).label('avg_confidence')
        ).where(
            Event.type.like('scene_%_interaction'),
            Event.created_at >= start_date
        ).group_by(func.substr(Event.type, 7, func.length(Event.type) - 17))
        
        interaction_result = await self.session.execute(interaction_stmt)
        interaction_stats = {
            row.scene: {
                'interactions': row.interactions,
                'avg_confidence': float(row.avg_confidence or 0)
            }
            for row in interaction_result
        }
        
        return {
            'period_days': days,
            'scene_transitions': transition_stats,
            'scene_interactions': interaction_stats,
            'total_scenes': len(self.scenes)
        }
