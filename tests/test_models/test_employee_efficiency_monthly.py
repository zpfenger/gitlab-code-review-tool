"""EmployeeEfficiencyMonthly 模型测试"""
import json
import os
import tempfile
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.employee_efficiency_monthly import EmployeeEfficiencyMonthly


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


def test_create_monthly_record(session):
    """创建月度记录，字段正确赋值"""
    row = EmployeeEfficiencyMonthly(
        author_email="a@b.com",
        author_name="Alice",
        year_month="2026-05",
        commits_count=50,
        additions=1000,
        deletions=500,
        files_changed=30,
        new_files=5,
        deleted_files=2,
        active_days=20,
        projects_involved=json.dumps(["proj-a", "proj-b"]),
        review_score=85,
        review_grade="良好",
        review_summary="本月整体良好",
        work_summary=json.dumps(["完成A", "修复B"]),
        summary_top_n=10,
        llm_status="success",
    )
    session.add(row)
    session.commit()

    saved = session.query(EmployeeEfficiencyMonthly).first()
    assert saved.author_email == "a@b.com"
    assert saved.year_month == "2026-05"
    assert saved.commits_count == 50
    assert saved.active_days == 20
    assert saved.review_score == 85
    assert json.loads(saved.projects_involved) == ["proj-a", "proj-b"]
    assert json.loads(saved.work_summary) == ["完成A", "修复B"]


def test_unique_constraint_email_month(session):
    """同人同月只能有一条记录（UPSERT 幂等基础）"""
    row1 = EmployeeEfficiencyMonthly(
        author_email="a@b.com", author_name="Alice",
        year_month="2026-05", commits_count=10,
    )
    session.add(row1)
    session.commit()

    row2 = EmployeeEfficiencyMonthly(
        author_email="a@b.com", author_name="Alice",
        year_month="2026-05", commits_count=20,
    )
    session.add(row2)
    with pytest.raises(Exception):  # IntegrityError
        session.commit()
    session.rollback()


def test_optional_llm_fields(session):
    """LLM 字段可为空（pending 状态）"""
    row = EmployeeEfficiencyMonthly(
        author_email="a@b.com", author_name="Alice",
        year_month="2026-05", commits_count=10,
        llm_status="pending",
    )
    session.add(row)
    session.commit()

    saved = session.query(EmployeeEfficiencyMonthly).first()
    assert saved.review_score is None
    assert saved.review_grade is None
    assert saved.review_summary is None
    assert saved.work_summary is None
    assert saved.llm_error is None


def test_repr(session):
    """__repr__ 包含关键信息"""
    row = EmployeeEfficiencyMonthly(
        author_email="a@b.com", author_name="Alice",
        year_month="2026-05", review_score=85,
    )
    assert "a@b.com" in repr(row)
    assert "2026-05" in repr(row)
