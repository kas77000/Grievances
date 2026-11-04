"""Desk settings: read the watchdog's config.toml so paths and kdb+ endpoints live in one place.

Lookup order: $ORDER_ALERT_CONFIG, ../watchdog/config.toml, then the bundled demo config.
"""
from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
WATCHDOG_DIR = APP_DIR.parent / "watchdog"
DEMO_CONFIG = APP_DIR / "demo" / "config.toml"

if str(WATCHDOG_DIR) not in sys.path:
    sys.path.insert(0, str(WATCHDOG_DIR))
import order_watchdog as wd  # noqa: E402  (parsing + connection picking are shared with the watchdog)


@dataclass
class DeskSettings:
    config_path: Path
    is_demo: bool
    watchdog: "wd.Config"
    team: list[str] = field(default_factory=list)
    history_days: int = 30


def config_path() -> Path:
    env = os.environ.get("ORDER_ALERT_CONFIG")
    if env:
        return Path(env)
    real = WATCHDOG_DIR / "config.toml"
    return real if real.exists() else DEMO_CONFIG


def load_settings() -> DeskSettings:
    path = config_path()
    raw = tomllib.loads(path.read_text(encoding="utf-8")).get("desk", {})
    return DeskSettings(
        config_path=path,
        is_demo=path.resolve() == DEMO_CONFIG.resolve(),
        watchdog=wd.load_config(path),
        team=[str(t) for t in raw.get("team", [])],
        history_days=int(raw.get("history_days", 30)),
    )
