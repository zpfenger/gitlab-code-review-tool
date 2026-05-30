"""测试日报任务完成后会触发能效聚合"""
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from app.services.efficiency_aggregator import EfficiencyAggregator


def test_aggregator_class_exposes_aggregate_method():
    """EfficiencyAggregator 暴露 aggregate 方法，hook 可以调用"""
    agg = EfficiencyAggregator(
        db=MagicMock(),
        gitlab_client_factory=lambda p: MagicMock(),
        llm_config={"api_url": "x", "api_key": "x", "model": "m"},
    )
    assert hasattr(agg, "aggregate")
    assert callable(agg.aggregate)


def test_daily_task_triggers_aggregator_with_yesterday(monkeypatch):
    """run_scheduled_task('daily') 完成所有项目后会用昨天日期调用 aggregator"""
    from app import main as main_mod

    # 1. mock 数据库返回的 settings 和 projects（projects 为空，避免触发真实的 daily review）
    fake_settings = MagicMock(
        global_gitlab_url="http://gl",
        global_gitlab_token=None,
        llm_api_url="x", llm_api_key=None, llm_model="m",
        llm_timeout=240, llm_max_retries=3, llm_retry_delay=10,
        daily_review_days=1,
    )
    # projects_list 非空（否则函数会在第一行 early return），
    # 但 project 内部 try 会捕获所有异常，循环安全跑完后才到 hook
    fake_project = MagicMock(access_token=None, name="p", project_id=1)
    fake_db = MagicMock()
    fake_db.query.return_value.filter.return_value.all.return_value = [fake_project]
    fake_db.query.return_value.first.return_value = fake_settings

    monkeypatch.setattr(main_mod, "SessionLocal", lambda: fake_db)

    # 2. mock EfficiencyAggregator
    aggregate_mock = MagicMock(return_value={
        "target_date": "x", "authors_total": 0,
        "authors_success": 0, "authors_failed": 0,
    })
    with patch("app.services.efficiency_aggregator.EfficiencyAggregator") as agg_cls:
        agg_cls.return_value.aggregate = aggregate_mock
        main_mod.run_scheduled_task("daily")

    # 3. 断言 aggregator 被昨天日期调用一次
    expected_date = date.today() - timedelta(days=1)
    aggregate_mock.assert_called_once_with(expected_date)


def test_weekly_task_does_not_trigger_aggregator(monkeypatch):
    """run_scheduled_task('weekly') 不应触发 aggregator"""
    from app import main as main_mod

    fake_settings = MagicMock(
        weekly_review_days=7,
        global_gitlab_url="http://gl",
        global_gitlab_token=None,
        llm_api_url="x", llm_api_key=None, llm_model="m",
        llm_timeout=240, llm_max_retries=3, llm_retry_delay=10,
    )
    fake_project = MagicMock(access_token=None, name="p", project_id=1)
    fake_db = MagicMock()
    fake_db.query.return_value.filter.return_value.all.return_value = [fake_project]
    fake_db.query.return_value.first.return_value = fake_settings

    monkeypatch.setattr(main_mod, "SessionLocal", lambda: fake_db)

    aggregate_mock = MagicMock()
    with patch("app.services.efficiency_aggregator.EfficiencyAggregator") as agg_cls:
        agg_cls.return_value.aggregate = aggregate_mock
        main_mod.run_scheduled_task("weekly")

    aggregate_mock.assert_not_called()
