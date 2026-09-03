"""Pydantic response schemas (docs/PROMPTS.md). Imported lazily by
agents/regime.py and agents/strategy.py inside their call-site functions to
avoid a module-load cycle (those modules define the enums these schemas
validate against).
"""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from src.agents.regime import RegimeLabel
from src.agents.strategy import StructureType


class RegimeResponse(BaseModel):
    label: RegimeLabel
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=20, max_length=600)


class StrategyLeg(BaseModel):
    occ_symbol: str
    side: str  # "buy" | "sell", checked below rather than via Literal so an
               # invalid value fails as a normal validation error with a
               # message the retry can act on

    def model_post_init(self, __context) -> None:
        if self.side not in ("buy", "sell"):
            raise ValueError(f"side must be 'buy' or 'sell', got {self.side!r}")


class StrategyResponse(BaseModel):
    decision: str  # "trade" | "decline"
    structure: StructureType | None = None
    legs: list[StrategyLeg] = []
    expiration: date | None = None
    rationale: str = Field(min_length=20, max_length=800)

    def model_post_init(self, __context) -> None:
        if self.decision not in ("trade", "decline"):
            raise ValueError(f"decision must be 'trade' or 'decline', got {self.decision!r}")
