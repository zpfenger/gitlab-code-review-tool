"""测试独立定时能效聚合任务"""
from datetime import date, timedelta
from unittest.mock import MagicMock, patch, PropertyMock

from app.services.efficiency_aggregator import EfficiencyAggregator
from app.services.efficiency_monthly_aggregator import EfficiencyMonthlyAggregator


def test_aggregator_class_exposes_aggregate_method():
    """EfficiencyAggregator 暴露 aggregate 方法"""
    agg = EfficiencyAggregator(
        db=MagicMock(),
        gitlab_client_factory=lambda p: MagicMock(),
        llm_config={"api_url": "x", "api_key": "x", "model": "m"},
    )
    assert hasattr(agg, "aggregate")
    assert callable(agg.aggregate)


def test_monthly_aggregator_class_exposes_aggregate_method():
    """EfficiencyMonthlyAggregator 暴露 aggregate 方法"""
    agg = EfficiencyMonthlyAggregator(
        db=MagicMock(),
        llm_config={"api_url": "x", "api_key": "x", "model": "m"},
    )
    assert hasattr(agg, "aggregate")
    assert callable(agg.aggregate)


def test_daily_efficiency_aggregation_with_yesterday(monkeypatch):
    """run_daily_efficiency_aggregation() 用昨天日期调用 aggregator"""
    from app import main as main_mod

    # mock 数据库返回的 settings
    fake_settings = MagicMock()
    fake_settings.efficiency_enabled = True
    fake_settings.efficiency_daily_enabled = True
    fake_settings.global_gitlab_url = "http://gl"
    fake_settings.global_gitlab_token = None
    fake_settings.llm_api_url = "x"
    fake_settings.llm_api_key = None
    fake_settings.llm_model = "m"
    fake_settings.llm_timeout = 240
    fake_settings.llm_max_retries = 3
    fake_settings.llm_retry_delay = 10
    fake_settings.review_max_tokens = 10000
    fake_settings.efficiency_work_summary_top_n = 5
    fake_settings.efficiency_prompt_template = None
    fake_settings.efficiency_excluded_emails = None
    fake_settings.efficiency_score_samples = 1
    # Mock property
    type(fake_settings).excluded_emails_list = PropertyMock(return_value=[])

    fake_db = MagicMock()
    fake_db.query.return_value.first.return_value = fake_settings

    # Patch app.database.SessionLocal 因为函数内部使用局部导入
    monkeypatch.setattr("app.database.SessionLocal", lambda: fake_db)

    # mock EfficiencyAggregator
    aggregate_mock = MagicMock(return_value={
        "target_date": "x", "authors_total": 0,
        "authors_success": 0, "authors_failed": 0,
    })
    with patch("app.services.efficiency_aggregator.EfficiencyAggregator") as agg_cls:
        agg_cls.return_value.aggregate = aggregate_mock
        main_mod.run_daily_efficiency_aggregation()

    # 断言 aggregator 被昨天日期调用一次
    expected_date = date.today() - timedelta(days=1)
    aggregate_mock.assert_called_once_with(expected_date)


def test_daily_efficiency_skipped_when_disabled(monkeypatch):
    """efficiency_daily_enabled=False 时跳过日常能效聚合"""
    from app import main as main_mod

    fake_settings = MagicMock()
    fake_settings.efficiency_enabled = True
    fake_settings.efficiency_daily_enabled = False  # 禁用

    fake_db = MagicMock()
    fake_db.query.return_value.first.return_value = fake_settings

    # Patch app.database.SessionLocal 因为函数内部使用局部导入
    monkeypatch.setattr("app.database.SessionLocal", lambda: fake_db)

    aggregate_mock = MagicMock()
    with patch("app.services.efficiency_aggregator.EfficiencyAggregator") as agg_cls:
        agg_cls.return_value.aggregate = aggregate_mock
        main_mod.run_daily_efficiency_aggregation()

    aggregate_mock.assert_not_called()


def test_daily_efficiency_skipped_when_efficiency_disabled(monkeypatch):
    """efficiency_enabled=False 时跳过日常能效聚合"""
    from app import main as main_mod

    fake_settings = MagicMock()
    fake_settings.efficiency_enabled = False  # 总开关禁用
    fake_settings.efficiency_daily_enabled = True

    fake_db = MagicMock()
    fake_db.query.return_value.first.return_value = fake_settings

    # Patch app.database.SessionLocal 因为函数内部使用局部导入
    monkeypatch.setattr("app.database.SessionLocal", lambda: fake_db)

    aggregate_mock = MagicMock()
    with patch("app.services.efficiency_aggregator.EfficiencyAggregator") as agg_cls:
        agg_cls.return_value.aggregate = aggregate_mock
        main_mod.run_daily_efficiency_aggregation()

    aggregate_mock.assert_not_called()


def test_monthly_efficiency_aggregation(monkeypatch):
    """run_monthly_efficiency_aggregation() 聚合上月数据"""
    from app import main as main_mod

    # mock 数据库返回的 settings
    fake_settings = MagicMock()
    fake_settings.efficiency_enabled = True
    fake_settings.efficiency_monthly_enabled = True
    fake_settings.llm_api_url = "x"
    fake_settings.llm_api_key = None
    fake_settings.llm_model = "m"
    fake_settings.llm_timeout = 240
    fake_settings.llm_max_retries = 3
    fake_settings.llm_retry_delay = 10
    fake_settings.review_max_tokens = 10000
    fake_settings.efficiency_work_summary_top_n = 5
    fake_settings.efficiency_monthly_prompt_template = None
    fake_settings.efficiency_excluded_emails = None
    # Mock property
    type(fake_settings).excluded_emails_list = PropertyMock(return_value=[])

    fake_db = MagicMock()
    fake_db.query.return_value.first.return_value = fake_settings

    # Patch app.database.SessionLocal 因为函数内部使用局部导入
    monkeypatch.setattr("app.database.SessionLocal", lambda: fake_db)

    # mock EfficiencyMonthlyAggregator
    aggregate_mock = MagicMock(return_value={
        "year_month": "x", "authors_total": 0,
        "authors_success": 0, "authors_failed": 0,
    })
    with patch("app.services.efficiency_monthly_aggregator.EfficiencyMonthlyAggregator") as agg_cls:
        agg_cls.return_value.aggregate = aggregate_mock
        main_mod.run_monthly_efficiency_aggregation()

    # 断言 aggregator 被上月年月调用
    today = date.today()
    if today.month == 1:
        expected_ym = f"{today.year - 1}-12"
    else:
        expected_ym = f"{today.year}-{today.month - 1:02d}"
    aggregate_mock.assert_called_once_with(expected_ym)


def test_monthly_efficiency_skipped_when_disabled(monkeypatch):
    """efficiency_monthly_enabled=False 时跳过月度能效聚合"""
    from app import main as main_mod

    fake_settings = MagicMock()
    fake_settings.efficiency_enabled = True
    fake_settings.efficiency_monthly_enabled = False  # 禁用

    fake_db = MagicMock()
    fake_db.query.return_value.first.return_value = fake_settings

    # Patch app.database.SessionLocal 因为函数内部使用局部导入
    monkeypatch.setattr("app.database.SessionLocal", lambda: fake_db)

    aggregate_mock = MagicMock()
    with patch("app.services.efficiency_monthly_aggregator.EfficiencyMonthlyAggregator") as agg_cls:
        agg_cls.return_value.aggregate = aggregate_mock
        main_mod.run_monthly_efficiency_aggregation()

    aggregate_mock.assert_not_called()


def test_monthly_efficiency_skipped_when_efficiency_disabled(monkeypatch):
    """efficiency_enabled=False 时跳过月度能效聚合"""
    from app import main as main_mod

    fake_settings = MagicMock()
    fake_settings.efficiency_enabled = False  # 总开关禁用
    fake_settings.efficiency_monthly_enabled = True

    fake_db = MagicMock()
    fake_db.query.return_value.first.return_value = fake_settings

    # Patch app.database.SessionLocal 因为函数内部使用局部导入
    monkeypatch.setattr("app.database.SessionLocal", lambda: fake_db)

    aggregate_mock = MagicMock()
    with patch("app.services.efficiency_monthly_aggregator.EfficiencyMonthlyAggregator") as agg_cls:
        agg_cls.return_value.aggregate = aggregate_mock
        main_mod.run_monthly_efficiency_aggregation()

    aggregate_mock.assert_not_called()


def test_daily_task_does_not_trigger_efficiency(monkeypatch):
    """run_scheduled_task('daily') 不再触发能效聚合"""
    from app import main as main_mod

    fake_settings = MagicMock()
    fake_settings.daily_review_days = 1
    fake_settings.global_gitlab_url = "http://gl"
    fake_settings.global_gitlab_token = None
    fake_settings.llm_api_url = "x"
    fake_settings.llm_api_key = None
    fake_settings.llm_model = "m"
    fake_settings.llm_timeout = 240
    fake_settings.llm_max_retries = 3
    fake_settings.llm_retry_delay = 10
    fake_settings.efficiency_enabled = True

    fake_project = MagicMock()
    fake_project.access_token = None
    fake_project.name = "p"
    fake_project.project_id = 1

    fake_db = MagicMock()
    fake_db.query.return_value.filter.return_value.all.return_value = [fake_project]
    fake_db.query.return_value.first.return_value = fake_settings

    # run_scheduled_task 使用模块级别的 SessionLocal
    monkeypatch.setattr(main_mod, "SessionLocal", lambda: fake_db)

    aggregate_mock = MagicMock()
    with patch("app.services.efficiency_aggregator.EfficiencyAggregator") as agg_cls:
        agg_cls.return_value.aggregate = aggregate_mock
        main_mod.run_scheduled_task("daily")

    # 日报任务不再触发能效聚合
    aggregate_mock.assert_not_called()
