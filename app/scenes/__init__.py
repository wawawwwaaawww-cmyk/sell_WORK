"""Scenes package for managing conversation scenarios.

This package contains the conversation scenario management system
that handles personalized dialogues based on user segments.
"""

from .base_scene import BaseScene, SceneState, SceneResponse
from .scene_manager import SceneManager

from .action_registry import ActionRegistry, ActionContext
from .config_loader import load_scenario_config, ScenarioConfig, ScenarioConfigError
from .newbie_scene import NewbieScene
from .trader_scene import TraderScene
from .investor_scene import InvestorScene
from .skeptic_scene import SkepticScene
from .strategy_scene import StrategyScene

__all__ = [
    "BaseScene",
    "SceneState", 
    "SceneResponse",
    "SceneManager",
    "NewbieScene", 
    "TraderScene",
    "InvestorScene",
    "SkepticScene",
    "StrategyScene",
]