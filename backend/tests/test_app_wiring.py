from apscheduler.schedulers.background import BackgroundScheduler
from fastapi.testclient import TestClient

from candly.api.app import create_app
from candly.jobs.pipeline import daily_pipeline, intraday_pipeline, nightly_scorecards, register_pipeline_jobs


def test_bad_query_param_type_returns_400(tmp_data_dir, no_keys):
    client = TestClient(create_app())
    response = client.get("/api/indicators", params={"instrument": "NSE:RELIANCE", "tf": "1D", "limit": "x"})
    assert response.status_code == 400
    assert "limit" in response.json()["detail"]


def test_pipeline_jobs_replace_platform_ingest_jobs():
    scheduler = BackgroundScheduler()
    scheduler.add_job(lambda: None, "interval", minutes=5, id="intraday_ingest")
    register_pipeline_jobs(scheduler)
    jobs = {job.id: job.func for job in scheduler.get_jobs()}
    assert jobs["intraday_ingest"] is intraday_pipeline
    assert jobs["daily_ingest_nse_bse"] is daily_pipeline
    assert jobs["daily_ingest_mcx"] is daily_pipeline
    assert jobs["nightly_scorecards"] is nightly_scorecards
