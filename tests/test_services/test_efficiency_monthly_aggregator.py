"""EfficiencyMonthlyAggregator 测试"""
import json
import os
import tempfile
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from unittest.mock import patch

from app.database import Base
from app.models.employee_efficiency import EmployeeEfficiencyDaily
from app.models.employee_efficiency_monthly import EmployeeEfficiencyMonthly
from app.services.efficiency_monthly_aggregator import EfficiencyMonthlyAggregator


@pytest.fixture
def engine():
    fd, path = tempfile.mkstemp(suffix=".db")
    eng = create_engine(f"sqlite:///{path}",
                        connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    yield eng
    eng.dispose()
    os.close(fd)
    os.unlink(path)


@pytest.fixture
def session(engine):
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    s = Session()
    yield s
    s.close()


def _seed_daily(session, email, name, d, *, score=80, commits=3,
                adds=100, dels=20, projects=None):
    session.add(EmployeeEfficiencyDaily(
        author_email=email, author_name=name, stat_date=d,
        commits_count=commits, additions=adds, deletions=dels,
        files_changed=5, new_files=0, deleted_files=0,
        projects_involved=json.dumps(projects or ["proj-a"]),
        review_score=score, review_grade="良好",
        review_summary="ok", work_summary=json.dumps(["A", "B"]),
        summary_top_n=5, llm_status="success",
    ))
    session.commit()


@pytest.fixture
def llm_mock():
    with patch("app.services.efficiency_monthly_aggregator.call_and_parse_monthly") as m:
        m.return_value = {
            "raw": "## 月度总分：85 分",
            "score": 85,
            "grade": "良好",
            "work_summary": ["完成A", "修复B"],
            "review_summary": "本月整体良好",
            "success": True,
        }
        yield m


def test_aggregate_single_author(session, llm_mock):
    """单作者多天数据聚合为一条月度记录"""
    _seed_daily(session, "a@b.com", "Alice", date(2026, 5, 1),
                score=80, commits=3, adds=100, dels=20)
    _seed_daily(session, "a@b.com", "Alice", date(2026, 5, 2),
                score=90, commits=5, adds=200, dels=50)

    agg = EfficiencyMonthlyAggregator(
        db=session,
        llm_config={"api_url": "x", "api_key": "x", "model": "m"},
    )
    result = agg.aggregate("2026-05")

    assert result["authors_total"] == 1
    assert result["authors_success"] == 1

    rows = session.query(EmployeeEfficiencyMonthly).all()
    assert len(rows) == 1
    r = rows[0]
    assert r.author_email == "a@b.com"
    assert r.year_month == "2026-05"
    assert r.commits_count == 8      # 3 + 5
    assert r.additions == 300        # 100 + 200
    assert r.deletions == 70         # 20 + 50
    assert r.active_days == 2
    assert r.review_score == 85      # 算术平均 (80+90)/2 = 85
    assert r.llm_status == "success"


def test_review_score_arithmetic_average(session, llm_mock):
    """review_score 取日度评分的算术平均值"""
    _seed_daily(session, "a@b.com", "Alice", date(2026, 5, 1), score=80)
    _seed_daily(session, "a@b.com", "Alice", date(2026, 5, 2), score=90)

    agg = EfficiencyMonthlyAggregator(
        db=session,
        llm_config={"api_url": "x", "api_key": "x", "model": "m"},
    )
    # mock 返回的 score 会被覆盖为算术平均
    llm_mock.return_value["score"] = 0  # 故意设为 0
    agg.aggregate("2026-05")

    row = session.query(EmployeeEfficiencyMonthly).first()
    # 算术平均: (80 + 90) / 2 = 85
    assert row.review_score == 85


def test_projects_merge_dedup(session, llm_mock):
    """多天涉及项目合并去重"""
    _seed_daily(session, "a@b.com", "Alice", date(2026, 5, 1),
                projects=["proj-a", "proj-b"])
    _seed_daily(session, "a@b.com", "Alice", date(2026, 5, 2),
                projects=["proj-b", "proj-c"])

    agg = EfficiencyMonthlyAggregator(
        db=session,
        llm_config={"api_url": "x", "api_key": "x", "model": "m"},
    )
    agg.aggregate("2026-05")

    row = session.query(EmployeeEfficiencyMonthly).first()
    projects = json.loads(row.projects_involved)
    assert set(projects) == {"proj-a", "proj-b", "proj-c"}


def test_upsert_idempotent(session, llm_mock):
    """重复聚合不产生重复行"""
    _seed_daily(session, "a@b.com", "Alice", date(2026, 5, 1), score=80)

    agg = EfficiencyMonthlyAggregator(
        db=session,
        llm_config={"api_url": "x", "api_key": "x", "model": "m"},
    )
    agg.aggregate("2026-05")
    agg.aggregate("2026-05")

    rows = session.query(EmployeeEfficiencyMonthly).all()
    assert len(rows) == 1


def test_llm_failure_records_error(session):
    """LLM 失败时统计数据入库，llm_status=failed"""
    _seed_daily(session, "a@b.com", "Alice", date(2026, 5, 1), score=80)

    with patch("app.services.efficiency_monthly_aggregator.call_and_parse_monthly") as m:
        m.return_value = {
            "raw": None, "score": 0, "grade": None,
            "work_summary": [], "review_summary": "",
            "success": False,
        }
        agg = EfficiencyMonthlyAggregator(
            db=session,
            llm_config={"api_url": "x", "api_key": "x", "model": "m"},
        )
        agg.aggregate("2026-05")

    row = session.query(EmployeeEfficiencyMonthly).first()
    assert row.commits_count == 3       # 统计数据正常入库
    assert row.llm_status == "failed"
    assert row.review_score is None


def test_multi_author_aggregation(session, llm_mock):
    """多作者各自聚合为独立月度记录"""
    _seed_daily(session, "a@b.com", "Alice", date(2026, 5, 1),
                score=80, commits=3, adds=100, dels=20)
    _seed_daily(session, "b@b.com", "Bob", date(2026, 5, 1),
                score=90, commits=5, adds=200, dels=50)

    agg = EfficiencyMonthlyAggregator(
        db=session,
        llm_config={"api_url": "x", "api_key": "x", "model": "m"},
    )
    result = agg.aggregate("2026-05")

    assert result["authors_total"] == 2
    assert result["authors_success"] == 2

    rows = session.query(EmployeeEfficiencyMonthly).all()
    assert len(rows) == 2
    emails = {r.author_email for r in rows}
    assert emails == {"a@b.com", "b@b.com"}


def test_empty_month_returns_zero(session, llm_mock):
    """无数据月份返回 0 作者"""
    agg = EfficiencyMonthlyAggregator(
        db=session,
        llm_config={"api_url": "x", "api_key": "x", "model": "m"},
    )
    result = agg.aggregate("2026-05")
    assert result["authors_total"] == 0
    assert result["authors_success"] == 0


def test_json_defensive_parsing(session, llm_mock):
    """projects_involved 或 work_summary 为无效 JSON 时优雅降级"""
    session.add(EmployeeEfficiencyDaily(
        author_email="a@b.com", author_name="Alice",
        stat_date=date(2026, 5, 1),
        commits_count=1, additions=10, deletions=2,
        files_changed=1, new_files=0, deleted_files=0,
        projects_involved="invalid-json",  # 故意无效
        review_score=80, review_grade="良好",
        review_summary="ok", work_summary="also-invalid",
        summary_top_n=5, llm_status="success",
    ))
    session.commit()

    agg = EfficiencyMonthlyAggregator(
        db=session,
        llm_config={"api_url": "x", "api_key": "x", "model": "m"},
    )
    # 不应抛异常
    result = agg.aggregate("2026-05")
    assert result["authors_total"] == 1
