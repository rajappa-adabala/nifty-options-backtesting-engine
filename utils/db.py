"""
utils/db.py
-----------
Optional PostgreSQL persistence layer.

Tables created automatically on first use:
  - trades       : One row per completed trade (summary)
  - trade_legs   : One row per leg of each trade
  - snapshots    : Market snapshots (for analysis / replay)

All DB calls are gated by config.USE_DB — if False, all functions are no-ops.
"""

import json
import logging
from datetime import datetime
from typing import Optional

import config
from backtester.models import Trade

logger = logging.getLogger(__name__)

# Lazy import — only needed if USE_DB = True
try:
    import psycopg2
    import psycopg2.extras
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False


_conn = None  # module-level connection (singleton)


def _get_conn():
    global _conn
    if _conn is None or _conn.closed:
        if not PSYCOPG2_AVAILABLE:
            raise RuntimeError("psycopg2 not installed. Run: pip install psycopg2-binary")
        _conn = psycopg2.connect(config.DB_URL)
        _conn.autocommit = False
    return _conn


def init_db() -> None:
    """Create tables if they don't exist."""
    if not config.USE_DB:
        return

    conn = _get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id            SERIAL PRIMARY KEY,
                trade_id      VARCHAR(20) UNIQUE NOT NULL,
                strategy      VARCHAR(100),
                symbol        VARCHAR(20),
                expiry        DATE,
                entry_time    TIMESTAMP,
                exit_time     TIMESTAMP,
                status        VARCHAR(30),
                premium_recv  NUMERIC(12,2),
                premium_paid  NUMERIC(12,2),
                gross_pnl     NUMERIC(12,2),
                total_costs   NUMERIC(12,2),
                net_pnl       NUMERIC(12,2),
                notes         TEXT,
                created_at    TIMESTAMP DEFAULT NOW()
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS trade_legs (
                id            SERIAL PRIMARY KEY,
                trade_id      VARCHAR(20) REFERENCES trades(trade_id),
                symbol        VARCHAR(20),
                expiry        DATE,
                strike        NUMERIC(10,2),
                option_type   VARCHAR(5),
                action        VARCHAR(5),
                qty           INT,
                lot_size      INT,
                entry_price   NUMERIC(10,2),
                exit_price    NUMERIC(10,2),
                gross_pnl     NUMERIC(12,2),
                brokerage     NUMERIC(10,2),
                slippage      NUMERIC(10,2),
                net_pnl       NUMERIC(12,2)
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS snapshots (
                id                SERIAL PRIMARY KEY,
                symbol            VARCHAR(20),
                expiry            DATE,
                timestamp         TIMESTAMP,
                underlying_price  NUMERIC(10,2),
                contracts_json    JSONB,
                created_at        TIMESTAMP DEFAULT NOW()
            );
        """)
        conn.commit()
    logger.info("DB tables initialised.")


def save_trade(trade: Trade) -> None:
    """Persist a completed trade and its legs to PostgreSQL."""
    if not config.USE_DB:
        return

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO trades
                  (trade_id, strategy, symbol, expiry, entry_time, exit_time, status,
                   premium_recv, premium_paid, gross_pnl, total_costs, net_pnl, notes)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (trade_id) DO UPDATE SET
                  exit_time = EXCLUDED.exit_time,
                  status    = EXCLUDED.status,
                  gross_pnl = EXCLUDED.gross_pnl,
                  net_pnl   = EXCLUDED.net_pnl;
            """, (
                trade.trade_id, trade.strategy_name, trade.symbol,
                trade.expiry.date(), trade.entry_time, trade.exit_time,
                trade.status.value,
                round(trade.total_premium_received, 2),
                round(trade.total_premium_paid_back, 2),
                round(trade.gross_pnl, 2),
                round(trade.total_costs, 2),
                round(trade.net_pnl, 2),
                trade.notes,
            ))

            for leg in trade.legs:
                cur.execute("""
                    INSERT INTO trade_legs
                      (trade_id, symbol, expiry, strike, option_type, action,
                       qty, lot_size, entry_price, exit_price,
                       gross_pnl, brokerage, slippage, net_pnl)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s);
                """, (
                    trade.trade_id,
                    leg.contract.symbol,
                    leg.contract.expiry.date(),
                    leg.contract.strike,
                    leg.contract.option_type.value,
                    leg.action.value,
                    leg.qty, leg.lot_size,
                    round(leg.entry_price, 2),
                    round(leg.exit_price, 2),
                    round(leg.realised_pnl, 2),
                    round(leg.brokerage, 2),
                    round(leg.slippage_applied, 2),
                    round(leg.net_pnl, 2),
                ))

        conn.commit()
        logger.debug(f"Trade {trade.trade_id} saved to DB.")
    except Exception as e:
        conn.rollback()
        logger.error(f"DB error saving trade {trade.trade_id}: {e}")
        raise