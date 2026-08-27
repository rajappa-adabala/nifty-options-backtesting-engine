"""
api/schemas.py
--------------
Pydantic models for request validation and response serialization.
Keeping these separate from backtester/models.py (internal dataclasses)
is intentional: API contracts should be able to evolve independently
of internal engine representations.
"""

from datetime import datetime, date
from enum import Enum
from typing import Optional, List

from pydantic import BaseModel, Field, field_validator


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class StrategyName(str, Enum):
    atm_straddle = "atm_straddle"


class BacktestRequest(BaseModel):
    """POST /backtest request body."""

    strategy: StrategyName = Field(..., description="Strategy to run")
    symbol: str = Field("NIFTY", description="Underlying: NIFTY or BANKNIFTY")
    from_date: date = Field(..., description="Backtest start date (YYYY-MM-DD)")
    to_date: date = Field(..., description="Backtest end date (YYYY-MM-DD)")
    stoploss_pct: Optional[float] = Field(
        None, ge=0, le=500, description="Stop-loss %% on premium received"
    )
    slippage_pct: float = Field(0.5, ge=0, le=10, description="Slippage %% per leg")
    lot_size: int = Field(50, gt=0, description="Contract lot size")
    parallel: bool = Field(True, description="Use multiprocessing across expiries")

    @field_validator("to_date")
    @classmethod
    def to_date_after_from_date(cls, v, info):
        from_date = info.data.get("from_date")
        if from_date and v < from_date:
            raise ValueError("to_date must be on or after from_date")
        return v

    class Config:
        json_schema_extra = {
            "example": {
                "strategy": "atm_straddle",
                "symbol": "NIFTY",
                "from_date": "2024-10-01",
                "to_date": "2024-12-31",
                "stoploss_pct": 50,
                "slippage_pct": 0.5,
                "lot_size": 50,
                "parallel": True,
            }
        }


class BacktestJobResponse(BaseModel):
    """Response after submitting a backtest job."""

    job_id: str
    status: JobStatus
    message: str


class TradeSummary(BaseModel):
    """Single trade row in the results."""

    trade_id: str
    expiry: date
    entry_time: datetime
    exit_time: Optional[datetime]
    status: str
    premium_received: float
    premium_paid_back: float
    gross_pnl: float
    total_costs: float
    net_pnl: float


class BacktestSummary(BaseModel):
    """Aggregate P&L summary."""

    total_trades: int
    wins: int
    losses: int
    win_rate_pct: float
    gross_pnl: float
    total_costs: float
    net_pnl: float
    avg_pnl_per_trade: float
    best_trade: float
    worst_trade: float
    max_drawdown: float


class BacktestResultResponse(BaseModel):
    """GET /backtest/{job_id} response."""

    job_id: str
    status: JobStatus
    config: BacktestRequest
    summary: Optional[BacktestSummary] = None
    error: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None


class TradeListResponse(BaseModel):
    """GET /backtest/{job_id}/trades response — paginated."""

    job_id: str
    total: int
    page: int
    page_size: int
    trades: List[TradeSummary]


class HealthResponse(BaseModel):
    status: str
    version: str