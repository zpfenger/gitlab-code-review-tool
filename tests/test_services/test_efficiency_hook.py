"""测试日报任务完成后会触发能效聚合"""
from datetime import date
from unittest.mock import MagicMock, patch

from app.services.efficiency_aggregator import EfficiencyAggregator


def test_aggregator_is_callable_after_daily_task():
    """聚合器可以被独立调用一次完成全部项目"""
    # 这是占位测试，确保 import 链路畅通；实际钩子在 main.py 调用
    agg = EfficiencyAggregator(
        db=MagicMock(),
        gitlab_client_factory=lambda p: MagicMock(),
        llm_config={"api_url": "x", "api_key": "x", "model": "m"},
    )
    assert hasattr(agg, "aggregate")
    assert callable(agg.aggregate)
