import logging

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi.testclient import TestClient

from candly.api.app import create_app
from candly.core.log import RedactAuthQuery
from candly.jobs import pipeline
from candly.jobs.pipeline import daily_pipeline, intraday_pipeline, nightly_scorecards, register_pipeline_jobs


def test_bad_query_param_type_returns_400():
    client = TestClient(create_app())
    response = client.get("/api/indicators", params={"instrument": "NSE:RELIANCE", "tf": "1D", "limit": "x"})
    assert response.status_code == 400
    assert "limit" in response.json()["detail"]


def test_untrusted_host_is_rejected():
    client = TestClient(create_app())
    assert client.get("/api/health", headers={"host": "evil.example"}).status_code == 400
    assert client.get("/api/health", headers={"host": "localhost:5173"}).status_code == 200


def test_access_log_drops_auth_query_strings():
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", "/api/auth/fyers/callback?auth_code=SECRET&state=x", "1.1", 307), None,
    )
    RedactAuthQuery().filter(record)
    assert "SECRET" not in record.getMessage()
    assert "/api/auth/fyers/callback" in record.getMessage()


def test_alert_routes_work_without_telegram_keys():
    client = TestClient(create_app())
    assert client.post("/api/alerts/test", content="{}").status_code == 415
    sent = client.post("/api/alerts/test", json={})
    assert sent.status_code == 200 and sent.json()["sent"] is False
    preview = client.get("/api/alerts/preview", params={"kind": "post_market"})
    assert preview.status_code == 200 and isinstance(preview.json()["text"], str)
    assert client.get("/api/alerts/preview", params={"kind": "nope"}).status_code == 400


def test_pipeline_jobs_replace_platform_ingest_jobs():
    scheduler = BackgroundScheduler()
    scheduler.add_job(lambda: None, "interval", minutes=5, id="intraday_ingest")
    register_pipeline_jobs(scheduler)
    jobs = {job.id: job.func for job in scheduler.get_jobs()}
    assert jobs["intraday_ingest"] is intraday_pipeline
    assert jobs["daily_ingest_nse_bse"] is daily_pipeline
    assert jobs["daily_ingest_mcx"] is daily_pipeline
    assert jobs["nightly_scorecards"] is nightly_scorecards
    assert jobs["tag_news"] is pipeline.tag_news


def _record_calls(monkeypatch, exchanges):
    calls = []
    monkeypatch.setattr(pipeline, "exchanges_with_closed_bar", lambda now: exchanges)
    monkeypatch.setattr(pipeline, "ingest_incremental", lambda tf, ex: calls.append(("ingest", tf, ex)))
    monkeypatch.setattr(
        pipeline, "run_forecast_cycle", lambda tf, ids: calls.append(("forecast", tf, len(ids)))
    )
    monkeypatch.setattr(pipeline, "grade_pending_job", lambda: calls.append(("grade",)))
    monkeypatch.setattr(
        pipeline.llm, "explain_recent_calls", lambda limit, tf: calls.append(("explain", tf))
    )
    monkeypatch.setattr(pipeline, "run_alert_checks", lambda: calls.append(("alerts",)))
    return calls


def test_intraday_pipeline_ingests_before_forecasting_then_grades(monkeypatch):
    calls = _record_calls(monkeypatch, ("MCX",))
    intraday_pipeline()
    assert [c[0] for c in calls] == ["ingest", "forecast"] * 3 + ["explain", "grade", "alerts"]
    assert ("explain", "1h") in calls  # only 1D/1h forecasts get explanations
    assert all(c[2] == ("MCX",) for c in calls if c[0] == "ingest")
    assert all(c[2] == 4 for c in calls if c[0] == "forecast")  # the four MCX instruments


def test_intraday_pipeline_does_nothing_when_markets_are_closed(monkeypatch):
    calls = _record_calls(monkeypatch, ())
    intraday_pipeline()
    assert calls == []


def test_daily_pipeline_order(monkeypatch):
    calls = _record_calls(monkeypatch, ())
    daily_pipeline(("NSE", "BSE"))
    assert [c[0] for c in calls] == ["ingest", "forecast", "explain", "grade", "alerts"]


def test_nightly_scorecards_build_every_exchange_and_survive_one_failure(monkeypatch):
    built = []

    def fake_rebuild(tf, *, exchange):
        if (tf, exchange) == ("1h", "BSE"):
            raise RuntimeError("boom")
        built.append((tf, exchange))

    monkeypatch.setattr(pipeline, "rebuild_scorecard", fake_rebuild)
    nightly_scorecards()
    every = [(tf, ex) for tf in ("1D", "1h", "15m", "5m") for ex in ("NSE", "BSE", "MCX")]
    assert built == [b for b in every if b != ("1h", "BSE")]
