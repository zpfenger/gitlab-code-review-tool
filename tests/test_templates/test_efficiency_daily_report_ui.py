from pathlib import Path


def test_efficiency_template_defines_daily_report_modal():
    """人员详情页应有独立的日报详情弹窗。"""
    template = Path("app/templates/efficiency.html").read_text(encoding="utf-8")

    assert 'id="dailyReportModal"' in template
    assert 'id="dailyReportModalTitle"' in template
    assert 'id="dailyReportModalBody"' in template
    assert 'id="dailyReportModalClose"' in template


def test_efficiency_js_renders_daily_reports_before_trend_and_removes_commits():
    """抽屉中日报入口应位于近 7 天趋势上方，不再展示今日提交区块。"""
    script = Path("app/static/js/efficiency.js").read_text(encoding="utf-8")

    assert "function renderDailyReports" in script
    assert "function openDailyReportModal" in script
    assert "当前人员审查报告-日报" in script
    assert script.index("当前人员审查报告-日报") < script.index("近 7 天趋势")
    assert "今日提交" not in script
