from __future__ import annotations

import importlib.util
import types
from pathlib import Path

from .settings import STRATEGY_DIR


__all__ = ["load_strategy"]


def load_strategy(name: str) -> types.ModuleType:
    strategy_dir = STRATEGY_DIR.resolve()
    if Path(name).parts != (name,):
        raise ValueError(f"Strategy name must be a plain file name without path separators: {name}")
    if name.endswith(".py"):
        raise ValueError(f"Strategy name must be given without the .py suffix: {name}")
    path = (STRATEGY_DIR / f"{name}.py").resolve()
    if path.parent != strategy_dir:
        raise ValueError(f"Strategy path must stay inside {strategy_dir}")
    if not path.exists():
        raise FileNotFoundError(f"Missing strategy file: {path}")
    spec = importlib.util.spec_from_file_location(f"user_strategy_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load strategy file: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not callable(getattr(module, "generate_signals", None)):
        raise ValueError(f"{path} must define generate_signals(df, asset, timeframe, params)")
    return module
