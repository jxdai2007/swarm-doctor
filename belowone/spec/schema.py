"""Operator goal specifications with strict, workspace-relative resource rules."""

from dataclasses import InitVar, dataclass, field
import math
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MappingProxyType
from typing import Mapping


class SpecValidationError(ValueError):
    """A goal specification is unsafe or does not match the schema."""


def _text(value, name):
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise SpecValidationError(f"{name} must be a nonempty string without NUL")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise SpecValidationError(f"{name} must contain valid Unicode") from error
    return value


def _texts(value, name, *, required=False):
    if not isinstance(value, (list, tuple)):
        raise SpecValidationError(f"{name} must be a list of strings")
    result = tuple(_text(item, f"{name}[{index}]") for index, item in enumerate(value))
    if required and not result:
        raise SpecValidationError(f"{name} must contain at least one criterion")
    if len(set(result)) != len(result):
        raise SpecValidationError(f"{name} must not contain duplicates")
    return result


def workspace_path(value, workspace, name, *, pattern=False):
    """Validate POSIX relative paths/globs, including existing symlink targets."""
    value = _text(value, name)
    relative = PurePosixPath(value)
    if (relative.is_absolute() or PureWindowsPath(value).drive
            or "\\" in value or ".." in relative.parts):
        raise SpecValidationError(f"{name} must stay inside workspace: {value!r}")
    if not pattern and any(char in value for char in "*?[]"):
        raise SpecValidationError(f"{name} must be a path, not a glob")
    root = Path(workspace).resolve()
    # Validate the literal prefix even when the glob currently matches nothing.
    prefix = root
    for part in relative.parts:
        if pattern and any(char in part for char in "*?["):
            break
        prefix /= part
    try:
        if not prefix.resolve().is_relative_to(root):
            raise SpecValidationError(f"{name} resolves outside workspace: {value!r}")
        if pattern and any(char in value for char in "*?["):
            for match in root.glob(value):
                if not match.resolve().is_relative_to(root):
                    raise SpecValidationError(f"{name} resolves outside workspace: {value!r}")
    except (ValueError, OSError, RuntimeError) as error:
        raise SpecValidationError(f"{name} has an unsafe or invalid workspace path: {value!r}") from error
    return value


@dataclass(frozen=True)
class GoalSpec:
    """Validated, immutable spec. Resources use workspace-relative POSIX paths.

    ``gray_zones`` maps descriptions to ``allow``/``deny``. ``compartments``
    maps agent IDs to path/glob lists. ``budgets`` contains ``steps_per_agent``
    (positive integer) and ``cost_usd`` (positive finite number). Serialized
    collections are dictionaries/lists; in-memory collections are immutable.
    Workspace is validation context, not part of the portable spec or its hash.
    """

    goal: str
    done_when: tuple[str, ...]
    hard_lines: tuple[str, ...] = ()
    gray_zones: Mapping[str, str] = field(default_factory=dict)
    compartments: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    budgets: Mapping[str, int | float] = field(
        default_factory=lambda: {"steps_per_agent": 20, "cost_usd": 15.0}
    )
    decoys: tuple[str, ...] = ()
    high_risk_paths: tuple[str, ...] = ()
    response_mode: str = "verify"
    trace_radius: int = 2
    workspace: InitVar[str | Path | None] = None
    _workspace: Path = field(init=False, repr=False, compare=False)

    def __post_init__(self, workspace):
        root = Path.cwd().resolve() if workspace is None else Path(workspace).resolve()
        object.__setattr__(self, "_workspace", root)
        _text(self.goal, "goal")
        for name in ("done_when", "hard_lines", "decoys", "high_risk_paths"):
            object.__setattr__(self, name, _texts(getattr(self, name), name, required=name == "done_when"))
        if not isinstance(self.gray_zones, Mapping):
            raise SpecValidationError("gray_zones must be an object mapping descriptions to allow/deny")
        gray_zones = {}
        for description, decision in self.gray_zones.items():
            _text(description, "gray_zones key")
            if not isinstance(decision, str) or decision not in {"allow", "deny"}:
                raise SpecValidationError(f"gray_zones[{description!r}] must be allow or deny")
            gray_zones[description] = decision
        object.__setattr__(self, "gray_zones", MappingProxyType(gray_zones))
        if not isinstance(self.compartments, Mapping):
            raise SpecValidationError("compartments must be an object mapping agent IDs to path/glob lists")
        compartments = {}
        for agent_id, patterns in self.compartments.items():
            _text(agent_id, "compartments agent ID")
            patterns = _texts(patterns, f"compartments[{agent_id!r}]", required=True)
            for pattern in patterns:
                workspace_path(pattern, root, f"compartments[{agent_id!r}]", pattern=True)
            compartments[agent_id] = patterns
        object.__setattr__(self, "compartments", MappingProxyType(compartments))
        if not isinstance(self.budgets, Mapping) or set(self.budgets) != {"steps_per_agent", "cost_usd"}:
            raise SpecValidationError("budgets must contain exactly steps_per_agent and cost_usd")
        steps, cost = self.budgets["steps_per_agent"], self.budgets["cost_usd"]
        if type(steps) is not int or steps <= 0:
            raise SpecValidationError("budgets.steps_per_agent must be a positive integer")
        if type(cost) not in (int, float):
            raise SpecValidationError("budgets.cost_usd must be a positive finite number")
        try:
            cost = float(cost)
        except OverflowError as error:
            raise SpecValidationError("budgets.cost_usd must be a finite number") from error
        if not math.isfinite(cost) or cost <= 0:
            raise SpecValidationError("budgets.cost_usd must be a positive finite number")
        object.__setattr__(self, "budgets", MappingProxyType({"steps_per_agent": steps, "cost_usd": cost}))
        for name in ("decoys", "high_risk_paths"):
            for path in getattr(self, name):
                workspace_path(path, root, name)
        if not isinstance(self.response_mode, str) or self.response_mode not in {"verify", "strict"}:
            raise SpecValidationError("response_mode must be verify or strict")
        if type(self.trace_radius) is not int or self.trace_radius < 0:
            raise SpecValidationError("trace_radius must be a nonnegative integer")

    @classmethod
    def from_dict(cls, data, *, workspace=None):
        """Validate an untrusted JSON/YAML object; unknown fields fail closed."""
        if not isinstance(data, dict):
            raise SpecValidationError("spec must be an object")
        names = {name for name, item in cls.__dataclass_fields__.items() if item.init and name != "workspace"}
        unknown = set(data) - names
        if unknown:
            raise SpecValidationError(f"unknown spec fields: {sorted(map(str, unknown))}")
        missing = {"goal", "done_when"} - set(data)
        if missing:
            raise SpecValidationError(f"missing required fields: {', '.join(sorted(missing))}")
        for name in ("done_when", "hard_lines", "decoys", "high_risk_paths"):
            if name in data and not isinstance(data[name], list):
                raise SpecValidationError(f"{name} must be a list of strings")
        for name in ("gray_zones", "compartments", "budgets"):
            if name in data and not isinstance(data[name], dict):
                raise SpecValidationError(f"{name} must be an object")
        for agent_id, patterns in data.get("compartments", {}).items():
            if not isinstance(patterns, list):
                raise SpecValidationError(f"compartments[{agent_id!r}] must be a list of strings")
        return cls(**data, workspace=workspace)

    @classmethod
    def one_line(cls, goal, *, workspace=None):
        """Explicit R28 ablation: completion follows the goal; other defaults remain."""
        return cls(goal=goal, done_when=[goal], workspace=workspace)

    def to_dict(self):
        """Return independent, JSON-serializable canonical field values."""
        return {
            "goal": self.goal,
            "done_when": list(self.done_when),
            "hard_lines": list(self.hard_lines),
            "gray_zones": dict(self.gray_zones),
            "compartments": {agent: list(paths) for agent, paths in self.compartments.items()},
            "budgets": dict(self.budgets),
            "decoys": list(self.decoys),
            "high_risk_paths": list(self.high_risk_paths),
            "response_mode": self.response_mode,
            "trace_radius": self.trace_radius,
        }
