"""EmployeeEfficiencyDaily 模型测试"""
from datetime import date, datetime
import json
import pytest
from sqlalchemy.exc import IntegrityError

from app.models.employee_efficiency import EmployeeEfficiencyDaily


def test_create_employee_efficiency(db_session):
    """可以正常创建一条人员能效记录"""
    record = EmployeeEfficiencyDaily(
        author_email="zhangsan@example.com",
        author_name="张三",
        stat_date=date(2026, 5, 27),
        commits_count=5,
        additions=230,
        deletions=45,
        files_changed=12,
        new_files=2,
        deleted_files=0,
        projects_involved=json.dumps(["proj-a", "proj-b"]),
        review_score=85,
        review_grade="良好",
        review_summary="代码质量良好",
        work_summary=json.dumps(["实现登录", "修复 X bug"]),
        summary_top_n=5,
        llm_status="success",
    )
    db_session.add(record)
    db_session.commit()
    db_session.refresh(record)

    assert record.id is not None
    assert record.created_at is not None
    assert record.updated_at is not None


def test_unique_email_date_constraint(db_session):
    """同一 email 同一天只能有一条记录"""
    r1 = EmployeeEfficiencyDaily(
        author_email="a@b.com", author_name="A", stat_date=date(2026, 5, 27),
        commits_count=1, additions=10, deletions=0, files_changed=1,
        new_files=0, deleted_files=0, projects_involved="[]",
        llm_status="pending",
    )
    db_session.add(r1)
    db_session.commit()

    r2 = EmployeeEfficiencyDaily(
        author_email="a@b.com", author_name="A", stat_date=date(2026, 5, 27),
        commits_count=2, additions=20, deletions=0, files_changed=1,
        new_files=0, deleted_files=0, projects_involved="[]",
        llm_status="pending",
    )
    db_session.add(r2)
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_grade_field_optional(db_session):
    """评分和等级可空（LLM 失败时）"""
    record = EmployeeEfficiencyDaily(
        author_email="b@c.com", author_name="B", stat_date=date(2026, 5, 27),
        commits_count=1, additions=10, deletions=0, files_changed=1,
        new_files=0, deleted_files=0, projects_involved="[]",
        llm_status="failed", llm_error="LLM timeout",
    )
    db_session.add(record)
    db_session.commit()
    assert record.review_score is None
    assert record.review_grade is None


def test_repr(db_session):
    """__repr__ 输出便于调试"""
    record = EmployeeEfficiencyDaily(
        author_email="x@y.com", author_name="X", stat_date=date(2026, 5, 27),
        commits_count=1, additions=10, deletions=0, files_changed=1,
        new_files=0, deleted_files=0, projects_involved="[]",
        llm_status="pending",
    )
    db_session.add(record)
    db_session.commit()
    s = repr(record)
    assert "x@y.com" in s
    assert "2026-05-27" in s
