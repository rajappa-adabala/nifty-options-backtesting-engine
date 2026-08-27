"""
api/main.py
-----------

FastAPI service wrapping the options backtesting engine.

Endpoints:

    GET    /health
    POST   /backtest
    GET    /backtest/{job_id}
    GET    /backtest/{job_id}/trades
    DELETE /backtest/{job_id}

Run from project root:

    uvicorn api.main:app --reload --port 8000

Swagger:

    http://127.0.0.1:8000/docs
"""

from __future__ import annotations

import logging
import traceback

from datetime import datetime

from fastapi import (
    BackgroundTasks,
    FastAPI,
    HTTPException,
    Query,
)

from fastapi.middleware.cors import CORSMiddleware


import config


from api.schemas import (
    BacktestJobResponse,
    BacktestRequest,
    BacktestResultResponse,
    BacktestSummary,
    HealthResponse,
    JobStatus,
    TradeListResponse,
    TradeSummary,
)

from api import jobs


from strategies import STRATEGY_REGISTRY


from backtester.engine import (
    BacktestEngine,
)

from backtester.parallel_engine import (
    ParallelBacktestEngine,
)


# =============================================================================
# Logging
# =============================================================================

logging.basicConfig(
    level=getattr(
        logging,
        config.LOG_LEVEL.upper(),
        logging.INFO,
    ),
    format=(
        "%(asctime)s "
        "%(levelname)s "
        "%(message)s"
    ),
)

logger = logging.getLogger(
    __name__
)


# =============================================================================
# FastAPI application
# =============================================================================

app = FastAPI(
    title="Options Backtesting API",

    description=(
        "REST API for running NSE options "
        "strategy backtests."
    ),

    version="1.0.0",
)


# =============================================================================
# CORS
# =============================================================================

app.add_middleware(
    CORSMiddleware,

    allow_origins=["*"],

    allow_methods=["*"],

    allow_headers=["*"],

    allow_credentials=False,
)


# =============================================================================
# Background backtest execution
# =============================================================================

def _run_backtest_job(
    job_id: str,
    request: BacktestRequest,
) -> None:
    """
    Execute a backtest in the background.

    POST /backtest returns immediately with a job ID.

    The background task:

        pending
          ↓
        running
          ↓
        completed

    or:

        running
          ↓
        failed
    """

    jobs.mark_running(
        job_id
    )

    try:

        # -----------------------------------------------------------------
        # Resolve strategy
        # -----------------------------------------------------------------

        strategy_key = (
            request.strategy.value
        )

        strategy_cls = (
            STRATEGY_REGISTRY[
                strategy_key
            ]
        )


        # -----------------------------------------------------------------
        # Apply current request risk/cost overrides
        # -----------------------------------------------------------------

        # NOTE:
        #
        # STOPLOSS_PCT and SLIPPAGE_PCT are still global because your
        # existing Portfolio / cost model currently reads config directly.
        #
        # This preserves existing behavior.
        #
        # Later we can refactor Portfolio so these are passed explicitly,
        # removing the concurrency limitation completely.

        if (
            request.stoploss_pct
            is not None
        ):

            config.STOPLOSS_PCT = (
                request.stoploss_pct
            )


        config.SLIPPAGE_PCT = (
            request.slippage_pct
        )


        # IMPORTANT:
        #
        # Do NOT do:
        #
        # config.LOT_SIZE = request.lot_size
        #
        # anymore.
        #
        # Lot size is passed directly into the engine.


        # -----------------------------------------------------------------
        # Convert API dates into datetime
        # -----------------------------------------------------------------

        start = datetime.combine(
            request.from_date,
            datetime.min.time(),
        )

        end = datetime.combine(
            request.to_date,
            datetime.min.time(),
        )


        logger.info(
            "Starting job %s | "
            "strategy=%s | "
            "symbol=%s | "
            "%s -> %s | "
            "lot=%d | "
            "parallel=%s",
            job_id,
            strategy_key,
            request.symbol,
            request.from_date,
            request.to_date,
            request.lot_size,
            request.parallel,
        )


        # -----------------------------------------------------------------
        # Create engine
        # -----------------------------------------------------------------

        if request.parallel:

            logger.info(
                "Job %s using ParallelBacktestEngine "
                "with max %d workers",
                job_id,
                config.PARALLEL_WORKERS,
            )


            engine = ParallelBacktestEngine(

                strategy_cls=(
                    strategy_cls
                ),

                symbol=(
                    request.symbol
                ),

                start_date=(
                    start
                ),

                end_date=(
                    end
                ),

                num_workers=(
                    config.PARALLEL_WORKERS
                ),

                lot_size=(
                    request.lot_size
                ),
            )


        else:

            logger.info(
                "Job %s using sequential BacktestEngine",
                job_id,
            )


            engine = BacktestEngine(

                strategy_cls=(
                    strategy_cls
                ),

                symbol=(
                    request.symbol
                ),

                start_date=(
                    start
                ),

                end_date=(
                    end
                ),

                lot_size=(
                    request.lot_size
                ),
            )


        # -----------------------------------------------------------------
        # Run backtest
        # -----------------------------------------------------------------

        portfolio = (
            engine.run()
        )


        # -----------------------------------------------------------------
        # Build summary
        # -----------------------------------------------------------------

        summary = (
            portfolio.summary()
        )


        # -----------------------------------------------------------------
        # Serialize trades
        # -----------------------------------------------------------------

        trades = [

            trade.to_dict()

            for trade
            in portfolio.closed_trades
        ]


        # -----------------------------------------------------------------
        # Mark completed
        # -----------------------------------------------------------------

        jobs.mark_completed(
            job_id,
            summary,
            trades,
        )


        logger.info(
            "Job %s completed: "
            "%d trades | "
            "net P&L ₹%.2f",
            job_id,
            len(trades),
            summary.get(
                "net_pnl",
                0,
            ),
        )


    except Exception as exc:

        logger.error(
            "Job %s failed: %s\n%s",
            job_id,
            exc,
            traceback.format_exc(),
        )


        jobs.mark_failed(
            job_id,
            str(exc),
        )


# =============================================================================
# Health
# =============================================================================

@app.get(
    "/health",
    response_model=HealthResponse,
    tags=["meta"],
)
def health() -> HealthResponse:
    """
    API liveness endpoint.
    """

    return HealthResponse(
        status="ok",
        version="1.0.0",
    )


# =============================================================================
# Submit backtest
# =============================================================================

@app.post(
    "/backtest",
    response_model=BacktestJobResponse,
    status_code=202,
    tags=["backtest"],
)
def submit_backtest(
    request: BacktestRequest,
    background_tasks: BackgroundTasks,
) -> BacktestJobResponse:
    """
    Submit a new backtest.

    Returns immediately with a job ID.

    Poll:

        GET /backtest/{job_id}

    for progress/results.
    """

    job_id = jobs.create_job(
        request
    )


    background_tasks.add_task(
        _run_backtest_job,
        job_id,
        request,
    )


    logger.info(
        "Backtest job submitted: %s",
        job_id,
    )


    return BacktestJobResponse(

        job_id=job_id,

        status=(
            JobStatus.PENDING
        ),

        message=(
            "Backtest submitted. "
            "Poll GET /backtest/{job_id} "
            "for status."
        ),
    )


# =============================================================================
# Get result
# =============================================================================

@app.get(
    "/backtest/{job_id}",
    response_model=BacktestResultResponse,
    tags=["backtest"],
)
def get_backtest_result(
    job_id: str,
) -> BacktestResultResponse:
    """
    Get status and summary for a backtest job.
    """

    job = jobs.get_job(
        job_id
    )


    if job is None:

        raise HTTPException(

            status_code=404,

            detail=(
                f"Job {job_id} not found"
            ),
        )


    summary = None


    if job["summary"]:

        summary = BacktestSummary(
            **job["summary"]
        )


    return BacktestResultResponse(

        job_id=(
            job["job_id"]
        ),

        status=(
            job["status"]
        ),

        config=(
            job["config"]
        ),

        summary=(
            summary
        ),

        error=(
            job["error"]
        ),

        created_at=(
            job["created_at"]
        ),

        completed_at=(
            job["completed_at"]
        ),
    )


# =============================================================================
# Trade details
# =============================================================================

@app.get(
    "/backtest/{job_id}/trades",
    response_model=TradeListResponse,
    tags=["backtest"],
)
def get_backtest_trades(

    job_id: str,

    page: int = Query(
        default=1,
        ge=1,
    ),

    page_size: int = Query(
        default=20,
        ge=1,
        le=200,
    ),

) -> TradeListResponse:
    """
    Return paginated trades for a completed backtest.
    """

    job = jobs.get_job(
        job_id
    )


    if job is None:

        raise HTTPException(

            status_code=404,

            detail=(
                f"Job {job_id} not found"
            ),
        )


    if (
        job["status"]
        != JobStatus.COMPLETED
    ):

        status_value = (
            job["status"].value

            if hasattr(
                job["status"],
                "value",
            )

            else str(
                job["status"]
            )
        )


        raise HTTPException(

            status_code=409,

            detail=(
                f"Job is {status_value}; "
                "trades are not yet available"
            ),
        )


    all_trades = (
        job["trades"] or []
    )


    start_index = (
        (page - 1)
        * page_size
    )

    end_index = (
        start_index
        + page_size
    )


    page_trades = (
        all_trades[
            start_index:
            end_index
        ]
    )


    trade_summaries = [

        TradeSummary(

            trade_id=(
                trade["trade_id"]
            ),

            expiry=(
                trade["expiry"]
            ),

            entry_time=(
                trade["entry_time"]
            ),

            exit_time=(
                trade.get(
                    "exit_time"
                )
                or None
            ),

            status=(
                trade["status"]
            ),

            premium_received=(
                trade[
                    "premium_received"
                ]
            ),

            premium_paid_back=(
                trade[
                    "premium_paid_back"
                ]
            ),

            gross_pnl=(
                trade[
                    "gross_pnl"
                ]
            ),

            total_costs=(
                trade[
                    "total_costs"
                ]
            ),

            net_pnl=(
                trade[
                    "net_pnl"
                ]
            ),
        )

        for trade
        in page_trades
    ]


    return TradeListResponse(

        job_id=job_id,

        total=len(
            all_trades
        ),

        page=page,

        page_size=page_size,

        trades=trade_summaries,
    )


# =============================================================================
# Delete job
# =============================================================================

@app.delete(
    "/backtest/{job_id}",
    status_code=204,
    tags=["backtest"],
)
def delete_backtest_job(
    job_id: str,
) -> None:
    """
    Remove a job from the in-memory job store.

    This does NOT cancel a currently executing worker.
    """

    job = jobs.get_job(
        job_id
    )


    if job is None:

        raise HTTPException(

            status_code=404,

            detail=(
                f"Job {job_id} not found"
            ),
        )


    with jobs._lock:

        jobs._jobs.pop(
            job_id,
            None,
        )


    logger.info(
        "Deleted backtest job %s",
        job_id,
    )