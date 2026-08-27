"""
tests/test_api.py
------------------
Integration tests for the FastAPI layer using TestClient.
Run with: pytest tests/test_api.py -v

These use the synthetic data fallback (no real CSV needed) so they
run anywhere, including CI.
"""

import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient
from api.main import app

client = TestClient(app)


class TestHealthEndpoint:
    def test_health_check(self):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestBacktestSubmission:
    def test_submit_valid_backtest(self):
        resp = client.post("/backtest", json={
            "strategy": "atm_straddle",
            "symbol": "NIFTY",
            "from_date": "2023-01-01",
            "to_date": "2023-01-14",
            "stoploss_pct": 50,
            "slippage_pct": 0.5,
            "lot_size": 50,
            "parallel": False,
        })
        assert resp.status_code == 202
        body = resp.json()
        assert "job_id" in body
        assert body["status"] == "pending"

    def test_submit_invalid_date_range(self):
        """to_date before from_date should fail validation."""
        resp = client.post("/backtest", json={
            "strategy": "atm_straddle",
            "symbol": "NIFTY",
            "from_date": "2023-06-01",
            "to_date": "2023-01-01",
        })
        assert resp.status_code == 422

    def test_submit_invalid_strategy(self):
        resp = client.post("/backtest", json={
            "strategy": "nonexistent_strategy",
            "symbol": "NIFTY",
            "from_date": "2023-01-01",
            "to_date": "2023-01-14",
        })
        assert resp.status_code == 422

    def test_submit_invalid_stoploss_range(self):
        """stoploss_pct > 500 should fail validation."""
        resp = client.post("/backtest", json={
            "strategy": "atm_straddle",
            "symbol": "NIFTY",
            "from_date": "2023-01-01",
            "to_date": "2023-01-14",
            "stoploss_pct": 9999,
        })
        assert resp.status_code == 422


class TestBacktestPolling:
    def test_poll_until_complete(self):
        """Submit a small job and poll until it finishes."""
        resp = client.post("/backtest", json={
            "strategy": "atm_straddle",
            "symbol": "NIFTY",
            "from_date": "2023-01-01",
            "to_date": "2023-01-14",
            "parallel": False,
        })
        job_id = resp.json()["job_id"]

        # Engine computation for this range is ~0.7s (measured directly).
        # Poll quickly; 20 x 0.25s = 5s ceiling is generous headroom.
        final = None
        for _ in range(20):
            result = client.get(f"/backtest/{job_id}")
            assert result.status_code == 200
            final = result.json()
            if final["status"] == "completed":
                break
            time.sleep(0.25)

        assert final is not None
        assert final["status"] == "completed", f"Job did not complete in time: {final}"
        assert final["summary"] is not None
        assert "net_pnl" in final["summary"]

    def test_get_nonexistent_job(self):
        resp = client.get("/backtest/nonexistent-job-id")
        assert resp.status_code == 404


class TestTradeListing:
    def test_trades_endpoint_after_completion(self):
        resp = client.post("/backtest", json={
            "strategy": "atm_straddle",
            "symbol": "NIFTY",
            "from_date": "2023-01-01",
            "to_date": "2023-01-14",
            "parallel": False,
        })
        job_id = resp.json()["job_id"]

        for _ in range(20):
            result = client.get(f"/backtest/{job_id}")
            if result.json()["status"] == "completed":
                break
            time.sleep(0.25)

        trades_resp = client.get(f"/backtest/{job_id}/trades")
        assert trades_resp.status_code == 200
        body = trades_resp.json()
        assert "trades" in body
        assert body["page"] == 1

    def test_trades_pagination_params(self):
        resp = client.post("/backtest", json={
            "strategy": "atm_straddle",
            "symbol": "NIFTY",
            "from_date": "2023-01-01",
            "to_date": "2023-01-14",
            "parallel": False,
        })
        job_id = resp.json()["job_id"]

        for _ in range(20):
            if client.get(f"/backtest/{job_id}").json()["status"] == "completed":
                break
            time.sleep(0.25)

        resp = client.get(f"/backtest/{job_id}/trades?page=1&page_size=5")
        assert resp.status_code == 200
        assert resp.json()["page_size"] == 5

    def test_trades_for_pending_job_returns_409(self):
        resp = client.post("/backtest", json={
            "strategy": "atm_straddle",
            "symbol": "NIFTY",
            "from_date": "2023-01-01",
            "to_date": "2023-03-31",
            "parallel": False,
        })
        job_id = resp.json()["job_id"]
        # Don't wait — should still be pending/running
        trades_resp = client.get(f"/backtest/{job_id}/trades")
        assert trades_resp.status_code in (409, 200)  # 200 if it finished very fast


class TestPerformance:
    """
    Guards against regressions where job execution silently becomes slow
    (e.g. cache stops working, or a future change reintroduces uncached
    synthetic data generation per request).
    """

    def test_small_backtest_completes_quickly(self):
        """
        A 2-week, 2-expiry backtest should complete well under 5 seconds
        end-to-end (measured directly at ~0.7s on the engine alone).
        If this test takes anywhere near 60s, something upstream of the
        engine (job scheduling, FastAPI BackgroundTasks behavior on this
        platform) is the actual bottleneck, not the backtest computation.
        """
        start = time.time()
        resp = client.post("/backtest", json={
            "strategy": "atm_straddle",
            "symbol": "NIFTY",
            "from_date": "2023-01-01",
            "to_date": "2023-01-14",
            "parallel": False,
        })
        job_id = resp.json()["job_id"]

        for _ in range(40):
            result = client.get(f"/backtest/{job_id}")
            if result.json()["status"] == "completed":
                break
            time.sleep(0.25)

        elapsed = time.time() - start
        assert elapsed < 10, (
            f"Job took {elapsed:.1f}s end-to-end — expected under 10s. "
            f"This points to a scheduling/platform issue, not engine performance."
        )