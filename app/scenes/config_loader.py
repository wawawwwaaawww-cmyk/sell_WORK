"""YAML configuration loader for scenario engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

import yaml


class ScenarioConfigError(Exception):
    """Raised when scenario configuration is invalid."""


@dataclass(frozen=True)
class MetricConfig:
    """Metric configuration descriptor."""

    name: str
    labels: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class GuardConfig:
    """Global guard/policy definition."""

    name: str
    prompt: str
    apply_to_states: Optional[Sequence[str]] = None


@dataclass(frozen=True)
class EntryStep:
    """Single action invoked on state enter."""

    action: str
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Transition:
    """Transition descriptor from current state to target."""

    trigger: str
    target: str
    conditions: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StateConfig:
    """State configuration including entry steps and transitions."""

    name: str
    description: str = ""
    entry_steps: List[EntryStep] = field(default_factory=list)
    transitions: List[Transition] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)

    def get_extra(self, key: str, default: Any = None) -> Any:
        """Return extra field stored in raw section."""
        return self.raw.get(key, default)


@dataclass(frozen=True)
class GlobalConfig:
    """Global scenario configuration section."""

    guards: List[GuardConfig] = field(default_factory=list)
    metrics: List[MetricConfig] = field(default_factory=list)


@dataclass(frozen=True)
class ScenarioConfig:
    """Root configuration describing entire scenario graph."""

    version: int
    metadata: Dict[str, Any]
    global_config: GlobalConfig
    states: Mapping[str, StateConfig]

    def get_state(self, name: str) -> StateConfig:
        if name not in self.states:
            raise ScenarioConfigError(f"State '{name}' not found in configuration")
        return self.states[name]

    @property
    def state_names(self) -> List[str]:
        return list(self.states.keys())


DEFAULT_SCENARIO_CONFIG_PATH = Path("config/scenario_transitions.yaml")


def _ensure_mapping(node: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(node, Mapping):
        raise ScenarioConfigError(f"Expected mapping for '{path}', got {type(node).__name__}")
    return node


def _parse_entry_steps(raw_steps: Optional[List[Any]], state_name: str) -> List[EntryStep]:
    steps: List[EntryStep] = []
    if not raw_steps:
        return steps
    if not isinstance(raw_steps, list):
        raise ScenarioConfigError(f"State '{state_name}': entry.steps must be a list")
    for idx, item in enumerate(raw_steps, start=1):
        if not isinstance(item, Mapping):
            raise ScenarioConfigError(
                f"State '{state_name}': step #{idx} must be a mapping"
            )
        action = item.get("action")
        if not action or not isinstance(action, str):
            raise ScenarioConfigError(
                f"State '{state_name}': step #{idx} must define string 'action'"
            )
        params = {k: v for k, v in item.items() if k != "action"}
        steps.append(EntryStep(action=action, params=params))
    return steps


def _parse_transitions(raw_transitions: Optional[List[Any]], state_name: str) -> List[Transition]:
    transitions: List[Transition] = []
    if not raw_transitions:
        return transitions
    if not isinstance(raw_transitions, list):
        raise ScenarioConfigError(f"State '{state_name}': transitions must be a list")
    for idx, item in enumerate(raw_transitions, start=1):
        if not isinstance(item, Mapping):
            raise ScenarioConfigError(
                f"State '{state_name}': transition #{idx} must be a mapping"
            )
        trigger = item.get("trigger")
        target = item.get("target")
        if not trigger or not isinstance(trigger, str):
            raise ScenarioConfigError(
                f"State '{state_name}': transition #{idx} missing string 'trigger'"
            )
        if not target or not isinstance(target, str):
            raise ScenarioConfigError(
                f"State '{state_name}': transition #{idx} missing string 'target'"
            )
        conditions = item.get("conditions")
        if conditions is not None and not isinstance(conditions, Mapping):
            raise ScenarioConfigError(
                f"State '{state_name}': transition #{idx} conditions must be mapping"
            )
        transitions.append(
            Transition(trigger=trigger, target=target, conditions=dict(conditions or {}))
        )
    return transitions


def _parse_guards(raw_guards: Optional[List[Any]]) -> List[GuardConfig]:
    guards: List[GuardConfig] = []
    if not raw_guards:
        return guards
    if not isinstance(raw_guards, list):
        raise ScenarioConfigError("global.guards must be a list")
    for idx, item in enumerate(raw_guards, start=1):
        if not isinstance(item, Mapping):
            raise ScenarioConfigError(f"global.guards[{idx}] must be a mapping")
        name = item.get("name")
        prompt = item.get("prompt")
        if not name or not isinstance(name, str):
            raise ScenarioConfigError(f"global.guards[{idx}] missing string 'name'")
        if not prompt or not isinstance(prompt, str):
            raise ScenarioConfigError(f"global.guards[{idx}] missing string 'prompt'")
        apply_to = item.get("apply_to_states")
        if apply_to is None or apply_to == "*":
            apply_to_states = None
        elif isinstance(apply_to, str):
            apply_to_states = (apply_to,)
        elif isinstance(apply_to, list) and all(isinstance(value, str) for value in apply_to):
            apply_to_states = tuple(apply_to)
        else:
            raise ScenarioConfigError("global.guards apply_to_states must be '*', string or list of strings")
        guards.append(
            GuardConfig(
                name=name,
                prompt=prompt,
                apply_to_states=apply_to_states,
            )
        )
    return guards


def _parse_metrics(raw_metrics: Optional[List[Any]]) -> List[MetricConfig]:
    metrics: List[MetricConfig] = []
    if not raw_metrics:
        return metrics
    if not isinstance(raw_metrics, list):
        raise ScenarioConfigError("global.metrics must be a list")
    for idx, item in enumerate(raw_metrics, start=1):
        if not isinstance(item, Mapping):
            raise ScenarioConfigError(f"global.metrics[{idx}] must be a mapping")
        name = item.get("name")
        if not name or not isinstance(name, str):
            raise ScenarioConfigError(f"global.metrics[{idx}] missing string 'name'")
        labels = item.get("labels") or []
        if not isinstance(labels, list) or not all(isinstance(l, str) for l in labels):
            raise ScenarioConfigError(
                f"global.metrics[{idx}] labels must be a list of strings"
            )
        metrics.append(MetricConfig(name=name, labels=list(labels)))
    return metrics


def _parse_states(raw_states: Mapping[str, Any]) -> Dict[str, StateConfig]:
    states: Dict[str, StateConfig] = {}
    for name, payload in raw_states.items():
        if not isinstance(payload, Mapping):
            raise ScenarioConfigError(f"State '{name}' must be defined as mapping")
        description = payload.get("description", "")
        entry = payload.get("entry", {})
        entry_steps = _parse_entry_steps(entry.get("steps") if isinstance(entry, Mapping) else None, name)
        transitions = _parse_transitions(payload.get("transitions"), name)
        # store extras (fields not processed)
        extras = {
            k: v
            for k, v in payload.items()
            if k not in {"description", "entry", "transitions"}
        }
        states[name] = StateConfig(
            name=name,
            description=description or "",
            entry_steps=entry_steps,
            transitions=transitions,
            raw=extras,
        )
    return states


def load_scenario_config(path: Optional[Path | str] = None) -> ScenarioConfig:
    """Load scenario configuration from YAML file."""
    config_path = Path(path) if path else DEFAULT_SCENARIO_CONFIG_PATH
    if not config_path.exists():
        raise FileNotFoundError(config_path)

    with config_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    if not isinstance(data, Mapping):
        raise ScenarioConfigError("Scenario config root must be a mapping")

    version = data.get("version")
    if version is None:
        raise ScenarioConfigError("Scenario config missing 'version'")
    if not isinstance(version, int):
        raise ScenarioConfigError("Scenario config 'version' must be an integer")

    metadata = data.get("metadata") or {}
    if not isinstance(metadata, Mapping):
        raise ScenarioConfigError("Scenario config 'metadata' must be a mapping")

    global_section = data.get("global", {})
    if global_section and not isinstance(global_section, Mapping):
        raise ScenarioConfigError("Scenario config 'global' must be a mapping")
    guards = _parse_guards(global_section.get("guards"))
    metrics = _parse_metrics(global_section.get("metrics"))
    global_config = GlobalConfig(guards=guards, metrics=metrics)

    raw_states = data.get("states")
    if raw_states is None:
        raise ScenarioConfigError("Scenario config missing 'states'")
    states_mapping = _ensure_mapping(raw_states, "states")
    states = _parse_states(states_mapping)
    if not states:
        raise ScenarioConfigError("Scenario config must define at least one state")

    return ScenarioConfig(
        version=version,
        metadata=dict(metadata),
        global_config=global_config,
        states=states,
    )


__all__ = [
    "ScenarioConfig",
    "ScenarioConfigError",
    "EntryStep",
    "Transition",
    "StateConfig",
    "GuardConfig",
    "MetricConfig",
    "GlobalConfig",
    "load_scenario_config",
    "DEFAULT_SCENARIO_CONFIG_PATH",
]
