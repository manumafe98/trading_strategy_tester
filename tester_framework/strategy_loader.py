from __future__ import annotations

import importlib.util
import types
from pathlib import Path

from .settings import STRATEGY_DIR


__all__ = ["load_strategy"]


def load_strategy(name: str) -> types.ModuleType:
    strategy_dir = STRATEGY_DIR.resolve()
    path = (STRATEGY_DIR / f"{Path(name).stem}.py").resolve()
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
