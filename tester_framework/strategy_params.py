from __future__ import annotations

import itertools
import math
import types


__all__ = [
    "RESERVED_PARAMS",
    "check_declared_params",
    "format_param_value",
    "params_label",
    "parse_param_value",
    "parse_strategy_params",
    "resolve_strategy_params",
]


# Every key the engine puts in a hook's params; a strategy parameter may not use one.
RESERVED_PARAMS = (
    "tick_size",
    "pip_size",
    "session",
    "trade",
    "risk_reward_ratio",
    "exit_mode",
    "operation",
    "risk_pct",
    "capital",
    "with_costs",
    "time_period",
    "data_source",
)
PARAM_VALUE_TYPES = (int, float, bool, str)


def check_declared_params(strategy: types.ModuleType, name: str) -> None:
    params = getattr(strategy, "PARAMS", {})
    if not isinstance(params, dict):
        raise ValueError(f"{name} PARAMS must be a dict of name -> default")
    reserved = [key for key in params if key in RESERVED_PARAMS]
    if reserved:
        raise ValueError(f"{name} PARAMS uses reserved names: {', '.join(reserved)}")
    for key, default in params.items():
        if default is not None and not isinstance(default, PARAM_VALUE_TYPES):
            raise ValueError(f"{name} PARAMS {key} default must be int, float, bool, str or None")
    param_types = getattr(strategy, "PARAM_TYPES", {})
    if not isinstance(param_types, dict):
        raise ValueError(f"{name} PARAM_TYPES must be a dict of name -> type")
    for key, value_type in param_types.items():
        if key not in params:
            raise ValueError(f"{name} PARAM_TYPES names undeclared parameter: {key}")
        if params[key] is not None:
            raise ValueError(f"{name} PARAM_TYPES entry {key} needs a None default")
        if value_type not in PARAM_VALUE_TYPES:
            raise ValueError(f"{name} PARAM_TYPES entry {key} must be int, float, bool or str")


def parse_strategy_params(text: str | None) -> dict[str, tuple[str, ...]]:
    """Splits `name=value;name=value` into raw values per name, in the order given."""
    parsed: dict[str, tuple[str, ...]] = {}
    for item in (text or "").split(";"):
        if not item.strip():
            continue
        key, sep, values = item.partition("=")
        key = key.strip()
        if not sep or not key:
            raise ValueError(f"--strategy_params items must look like name=value; got: {item.strip()}")
        if key in parsed:
            raise ValueError(f"--strategy_params names {key} more than once")
        parsed[key] = tuple(value.strip() for value in values.split(","))
    return parsed


def _typed_value(raw: str, value_type: type) -> object:
    # Raises ValueError for text that is not a value of `value_type`.
    if not raw or raw.lower() == "none":
        raise ValueError
    if value_type is bool:
        if raw.lower() not in ("true", "false"):
            raise ValueError
        return raw.lower() == "true"
    if value_type is float:
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError
        return value
    return value_type(raw)


def parse_param_value(key: str, raw: str, default: object, value_type: type | None = None) -> object:
    """Parses one raw value to the type of its default, or of its PARAM_TYPES entry for a None default."""
    if default is None and raw.lower() == "none":
        return None
    value_type = type(default) if default is not None else value_type or str
    try:
        return _typed_value(raw, value_type)
    except ValueError:
        expected = value_type.__name__ if default is not None else f"none or {value_type.__name__}"
        raise ValueError(f"--strategy_params {key}={raw}: expected {expected}") from None


def format_param_value(value: object) -> str:
    """A parameter value spelled the way `--strategy_params` takes it."""
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def params_label(defaults: dict[str, object], params: dict[str, object]) -> str:
    """The `name=value` pairs of the parameters that differ from their defaults."""
    return " ".join(f"{key}={format_param_value(value)}" for key, value in params.items() if value != defaults[key])


def resolve_strategy_params(strategies: dict[str, types.ModuleType], text: str | None) -> dict[str, list[dict[str, object]]]:
    """Every parameter combination of each strategy: defaults merged with each `--strategy_params` combination.

    Combinations are the cartesian product of the listed values, in the order the names were given.
    """
    overrides = parse_strategy_params(text)
    resolved = {}
    for name, strategy in strategies.items():
        declared = getattr(strategy, "PARAMS", {})
        param_types = getattr(strategy, "PARAM_TYPES", {})
        for key in overrides:
            if key not in declared:
                raise ValueError(f"--strategy_params {key} is not declared in {name} PARAMS")
        values = [
            [parse_param_value(key, raw, declared[key], param_types.get(key)) for raw in raws]
            for key, raws in overrides.items()
        ]
        combinations = []
        for combination in itertools.product(*values):
            params = {**declared, **dict(zip(overrides, combination))}
            validate_params = getattr(strategy, "validate_params", None)
            if callable(validate_params):
                try:
                    validate_params(dict(params))
                except ValueError as exc:
                    raise ValueError(f"{name}: {exc}") from None
            combinations.append(params)
        resolved[name] = combinations
    return resolved
