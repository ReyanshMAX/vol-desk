"""Typed config loading and validation. Fails loudly on a missing key.

Reads config/params.yaml + config/universe.yaml + environment variables per
docs/INTEGRATIONS.md. Config is immutable for the process lifetime (no hot
reload, per docs/ARCHITECTURE.md) -- changes take effect on restart only.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class ConfigError(RuntimeError):
    """Raised on any missing, malformed, or unresolved config value."""


@dataclass(frozen=True)
class SymbolConfig:
    ticker: str
    cluster: str
    strike_increment: float | None  # None until Q-003 is resolved


@dataclass(frozen=True)
class LLMTiers:
    fast: str | None
    reasoning: str | None


@dataclass(frozen=True)
class Config:
    # raw sections, kept as plain dicts so params.yaml stays the single
    # source of truth for literals rather than being re-typed field by field
    signal: dict[str, Any]
    regime: dict[str, Any]
    strategy: dict[str, Any]
    management: dict[str, Any]
    data: dict[str, Any]
    risk: dict[str, Any]
    correlation_clusters: dict[str, list[str]]
    execution: dict[str, Any]
    trading_window: dict[str, str]
    cadences: dict[str, int]
    llm_max_retries: int
    llm_temperature: float
    llm_regime_max_tokens: int
    llm_strategy_max_tokens: int
    llm_timeout_s: int
    llm_tiers: LLMTiers

    symbols: list[SymbolConfig]

    alpaca_api_key: str
    alpaca_secret_key: str
    alpaca_mcp_command: str
    groq_api_key: str
    db_path: str
    config_dir: str
    log_level: str

    def symbol_tickers(self) -> list[str]:
        return [s.ticker for s in self.symbols]

    def symbol(self, ticker: str) -> SymbolConfig:
        for s in self.symbols:
            if s.ticker == ticker:
                return s
        raise ConfigError(f"unknown symbol {ticker!r}, not in universe.yaml")


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"required environment variable {name} is unset")
    return value


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"missing config file: {path}")
    with path.open() as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ConfigError(f"{path} did not parse to a mapping")
    return data


def _require_section(data: dict[str, Any], key: str, path: Path) -> dict[str, Any]:
    if key not in data:
        raise ConfigError(f"{path}: missing required section '{key}'")
    section = data[key]
    if not isinstance(section, dict):
        raise ConfigError(f"{path}: section '{key}' must be a mapping")
    return section


def load() -> Config:
    """Load and validate config/params.yaml + config/universe.yaml + env.

    Raises ConfigError on any missing key. Does not silently substitute a
    default for anything that must be explicitly set (CLAUDE.md rule 3).
    """
    config_dir = Path(os.environ.get("VOL_DESK_CONFIG", "./config"))
    params_path = config_dir / "params.yaml"
    universe_path = config_dir / "universe.yaml"

    params = _read_yaml(params_path)
    universe = _read_yaml(universe_path)

    required_sections = [
        "signal", "regime", "strategy", "management", "data", "risk",
        "correlation_clusters", "execution", "trading_window", "cadences",
        "llm",
    ]
    sections = {k: _require_section(params, k, params_path) for k in required_sections}

    llm = sections["llm"]
    for key in ("max_retries", "temperature", "regime_max_tokens",
                "strategy_max_tokens", "timeout_s", "tiers"):
        if key not in llm:
            raise ConfigError(f"{params_path}: llm.{key} is required")
    tiers_raw = llm["tiers"]
    if "fast" not in tiers_raw or "reasoning" not in tiers_raw:
        raise ConfigError(f"{params_path}: llm.tiers must define 'fast' and 'reasoning'")
    llm_tiers = LLMTiers(fast=tiers_raw["fast"], reasoning=tiers_raw["reasoning"])
    if llm_tiers.fast is None or llm_tiers.reasoning is None:
        # Not fatal at load time: Q-004 (Groq model IDs) is an open question
        # and regime_refresh / entry_scan degrade per D-010 when unresolved.
        # The LLM client refuses to make a call with an unset tier rather
        # than silently picking a model string nobody confirmed.
        pass

    if "symbols" not in universe or not isinstance(universe["symbols"], list):
        raise ConfigError(f"{universe_path}: missing 'symbols' list")
    symbols: list[SymbolConfig] = []
    for entry in universe["symbols"]:
        for key in ("ticker", "cluster", "strike_increment"):
            if key not in entry:
                raise ConfigError(f"{universe_path}: symbol entry missing '{key}': {entry}")
        symbols.append(SymbolConfig(
            ticker=entry["ticker"],
            cluster=entry["cluster"],
            strike_increment=entry["strike_increment"],
        ))
    if not symbols:
        raise ConfigError(f"{universe_path}: universe is empty")

    return Config(
        signal=sections["signal"],
        regime=sections["regime"],
        strategy=sections["strategy"],
        management=sections["management"],
        data=sections["data"],
        risk=sections["risk"],
        correlation_clusters=sections["correlation_clusters"],
        execution=sections["execution"],
        trading_window=sections["trading_window"],
        cadences=sections["cadences"],
        llm_max_retries=int(llm["max_retries"]),
        llm_temperature=float(llm["temperature"]),
        llm_regime_max_tokens=int(llm["regime_max_tokens"]),
        llm_strategy_max_tokens=int(llm["strategy_max_tokens"]),
        llm_timeout_s=int(llm["timeout_s"]),
        llm_tiers=llm_tiers,
        symbols=symbols,
        alpaca_api_key=_require_env("ALPACA_API_KEY"),
        alpaca_secret_key=_require_env("ALPACA_SECRET_KEY"),
        alpaca_mcp_command=_require_env("ALPACA_MCP_COMMAND"),
        groq_api_key=_require_env("GROQ_API_KEY"),
        db_path=os.environ.get("VOL_DESK_DB", "./vol-desk.db"),
        config_dir=str(config_dir),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
    )
