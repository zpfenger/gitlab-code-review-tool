"""EfficiencyAggregator 测试 — 聚合逻辑"""
from datetime import date, datetime
import json
from unittest.mock import MagicMock, patch

import pytest

from app.models.employee_efficiency import EmployeeEfficiencyDaily
from app.models.project import Project
from app.models.token_usage import TokenUsageLog
from app.services.efficiency_aggregator import EfficiencyAggregator
from app.services.llm_usage import TokenUsage


def _make_commit(sha, author_email, author_name, additions=10, deletions=2):
    """构造一个假 commit dict（GitLab API 返回结构）"""
    return {
        "id": sha,
        "author_email": author_email,
        "author_name": author_name,
        "message": f"commit msg {sha[:6]}",
        "_diffs": [
            {"diff": "+x\n+y\n-z", "new_path": "a.py", "old_path": "a.py",
             "new_file": False, "deleted_file": False, "renamed_file": False},
        ],
    }


@pytest.fixture
def gitlab_client_factory():
    """工厂：返回 mock GitLabClient，可注入 commits 和 diffs"""
    def _factory(commits_by_branch, diffs_by_sha):
        client = MagicMock()
        client.get_branches.return_value = [
            {"name": br} for br in commits_by_branch
        ]
        client.get_commits.side_effect = lambda project_id, since=None, until=None, \
            ref_name=None, exclude_merge_commits=True, **kw: \
            commits_by_branch.get(ref_name, [])
        client.get_commit_diff.side_effect = lambda project_id, sha: \
            diffs_by_sha.get(sha, [])
        return client
    return _factory


@pytest.fixture
def llm_mock():
    """mock call_and_parse"""
    with patch("app.services.efficiency_aggregator.call_and_parse") as m:
        m.return_value = {
            "raw": "mock raw output",
            "score": 85,
            "grade": "良好",
            "work_summary": ["实现 A", "修复 B"],
            "review_summary": "整体质量良好",
            "success": True,
        }
        yield m


def test_aggregate_single_project_single_author(
    db_session, gitlab_client_factory, llm_mock
):
    """单项目单作者：累加 commits/additions/deletions，调 1 次 LLM"""
    project = Project(name="proj-a", project_id=1, gitlab_url="http://gl",
                       is_active=True)
    db_session.add(project)
    db_session.commit()

    commits = {
        "main": [_make_commit("sha1", "a@b.com", "Alice")],
    }
    diffs = {
        "sha1": [{"diff": "+a\n+b\n-c", "new_path": "x.py", "old_path": "x.py",
                  "new_file": False, "deleted_file": False, "renamed_file": False}],
    }
    client = gitlab_client_factory(commits, diffs)

    agg = EfficiencyAggregator(
        db=db_session,
        gitlab_client_factory=lambda p: client,
        llm_config={"api_url": "x", "api_key": "x", "model": "m"},
    )
    agg.aggregate(date(2026, 5, 27))

    rows = db_session.query(EmployeeEfficiencyDaily).all()
    assert len(rows) == 1
    r = rows[0]
    assert r.author_email == "a@b.com"
    assert r.author_name == "Alice"
    assert r.commits_count == 1
    assert r.additions == 2   # +a, +b
    assert r.deletions == 1   # -c
    assert r.files_changed == 1
    assert r.review_score == 85
    assert r.review_grade == "良好"
    assert r.llm_status == "success"
    assert json.loads(r.work_summary) == ["实现 A", "修复 B"]
    assert json.loads(r.projects_involved) == ["proj-a"]


def test_cross_branch_dedup(db_session, gitlab_client_factory, llm_mock):
    """同 sha 在多分支只算一次"""
    project = Project(name="proj-a", project_id=1, gitlab_url="http://gl",
                       is_active=True)
    db_session.add(project)
    db_session.commit()

    same_commit = _make_commit("sha-shared", "a@b.com", "Alice")
    commits = {
        "main": [same_commit],
        "feature-x": [same_commit],
    }
    diffs = {
        "sha-shared": [{"diff": "+a", "new_path": "x.py", "old_path": "x.py",
                         "new_file": False, "deleted_file": False, "renamed_file": False}],
    }
    client = gitlab_client_factory(commits, diffs)

    agg = EfficiencyAggregator(
        db=db_session,
        gitlab_client_factory=lambda p: client,
        llm_config={"api_url": "x", "api_key": "x", "model": "m"},
    )
    agg.aggregate(date(2026, 5, 27))

    rows = db_session.query(EmployeeEfficiencyDaily).all()
    assert len(rows) == 1
    assert rows[0].commits_count == 1
    assert rows[0].additions == 1


def test_cross_project_merge(db_session, gitlab_client_factory, llm_mock):
    """同人多项目：合并为一行，projects_involved 列出两个项目"""
    p1 = Project(name="proj-a", project_id=1, gitlab_url="http://gl",
                 is_active=True)
    p2 = Project(name="proj-b", project_id=2, gitlab_url="http://gl",
                 is_active=True)
    db_session.add_all([p1, p2])
    db_session.commit()

    clients = {
        1: gitlab_client_factory(
            {"main": [_make_commit("sha-a", "a@b.com", "Alice")]},
            {"sha-a": [{"diff": "+a", "new_path": "f1", "old_path": "f1",
                         "new_file": False, "deleted_file": False, "renamed_file": False}]},
        ),
        2: gitlab_client_factory(
            {"main": [_make_commit("sha-b", "a@b.com", "Alice")]},
            {"sha-b": [{"diff": "+b\n+c", "new_path": "f2", "old_path": "f2",
                         "new_file": False, "deleted_file": False, "renamed_file": False}]},
        ),
    }

    agg = EfficiencyAggregator(
        db=db_session,
        gitlab_client_factory=lambda project: clients[project.project_id],
        llm_config={"api_url": "x", "api_key": "x", "model": "m"},
    )
    agg.aggregate(date(2026, 5, 27))

    rows = db_session.query(EmployeeEfficiencyDaily).all()
    assert len(rows) == 1
    r = rows[0]
    assert r.commits_count == 2
    assert r.additions == 3
    assert set(json.loads(r.projects_involved)) == {"proj-a", "proj-b"}


def test_skip_empty_email(db_session, gitlab_client_factory, llm_mock):
    """email 为空的 commit 被忽略"""
    project = Project(name="proj-a", project_id=1, gitlab_url="http://gl",
                       is_active=True)
    db_session.add(project)
    db_session.commit()

    commits = {
        "main": [
            _make_commit("sha1", "", "NoEmail"),
            _make_commit("sha2", "a@b.com", "Alice"),
        ],
    }
    diffs = {
        "sha1": [{"diff": "+a", "new_path": "x", "old_path": "x",
                  "new_file": False, "deleted_file": False, "renamed_file": False}],
        "sha2": [{"diff": "+b", "new_path": "y", "old_path": "y",
                  "new_file": False, "deleted_file": False, "renamed_file": False}],
    }
    client = gitlab_client_factory(commits, diffs)
    agg = EfficiencyAggregator(
        db=db_session,
        gitlab_client_factory=lambda p: client,
        llm_config={"api_url": "x", "api_key": "x", "model": "m"},
    )
    agg.aggregate(date(2026, 5, 27))

    rows = db_session.query(EmployeeEfficiencyDaily).all()
    assert len(rows) == 1
    assert rows[0].author_email == "a@b.com"


def test_llm_failure_records_error(db_session, gitlab_client_factory):
    """LLM 失败时代码量入库，llm_status=failed"""
    project = Project(name="proj-a", project_id=1, gitlab_url="http://gl",
                       is_active=True)
    db_session.add(project)
    db_session.commit()

    commits = {"main": [_make_commit("sha1", "a@b.com", "Alice")]}
    diffs = {"sha1": [{"diff": "+a", "new_path": "x", "old_path": "x",
                       "new_file": False, "deleted_file": False, "renamed_file": False}]}
    client = gitlab_client_factory(commits, diffs)

    with patch("app.services.efficiency_aggregator.call_and_parse") as m:
        m.return_value = {
            "raw": None, "score": 0, "grade": None,
            "work_summary": [], "review_summary": "",
            "success": False,
        }
        agg = EfficiencyAggregator(
            db=db_session,
            gitlab_client_factory=lambda p: client,
            llm_config={"api_url": "x", "api_key": "x", "model": "m"},
        )
        agg.aggregate(date(2026, 5, 27))

    rows = db_session.query(EmployeeEfficiencyDaily).all()
    assert len(rows) == 1
    assert rows[0].additions == 1
    assert rows[0].llm_status == "failed"
    assert rows[0].review_score is None


def test_upsert_idempotent(db_session, gitlab_client_factory, llm_mock):
    """同一天重复跑：覆盖更新，不产生重复行"""
    project = Project(name="proj-a", project_id=1, gitlab_url="http://gl",
                       is_active=True)
    db_session.add(project)
    db_session.commit()

    commits = {"main": [_make_commit("sha1", "a@b.com", "Alice")]}
    diffs = {"sha1": [{"diff": "+a", "new_path": "x", "old_path": "x",
                       "new_file": False, "deleted_file": False, "renamed_file": False}]}
    client = gitlab_client_factory(commits, diffs)

    agg = EfficiencyAggregator(
        db=db_session,
        gitlab_client_factory=lambda p: client,
        llm_config={"api_url": "x", "api_key": "x", "model": "m"},
    )
    agg.aggregate(date(2026, 5, 27))
    agg.aggregate(date(2026, 5, 27))   # 第二次

    rows = db_session.query(EmployeeEfficiencyDaily).all()
    assert len(rows) == 1


def test_successful_llm_usage_is_recorded(db_session, gitlab_client_factory):
    """LLM 成功且返回 usage 时，记录 efficiency token 消耗"""
    project = Project(name="proj-a", project_id=1, gitlab_url="http://gl",
                       is_active=True)
    db_session.add(project)
    db_session.commit()

    commits = {"main": [_make_commit("sha1", "a@b.com", "Alice")]}
    diffs = {"sha1": [{"diff": "+a", "new_path": "x", "old_path": "x",
                       "new_file": False, "deleted_file": False, "renamed_file": False}]}
    client = gitlab_client_factory(commits, diffs)
    usage = TokenUsage(
        model="gpt-4",
        prompt_tokens=20,
        completion_tokens=10,
        total_tokens=30,
    )

    with patch("app.services.efficiency_aggregator.call_and_parse") as m:
        m.return_value = {
            "raw": "mock raw output",
            "score": 85,
            "grade": "良好",
            "work_summary": ["实现 A"],
            "review_summary": "整体质量良好",
            "success": True,
            "usage": usage,
        }
        agg = EfficiencyAggregator(
            db=db_session,
            gitlab_client_factory=lambda p: client,
            llm_config={"api_url": "x", "api_key": "x", "model": "m"},
        )
        agg.aggregate(date(2026, 5, 27))

    efficiency_row = db_session.query(EmployeeEfficiencyDaily).one()
    usage_row = db_session.query(TokenUsageLog).one()
    assert usage_row.biz_type == "efficiency"
    assert usage_row.biz_id == efficiency_row.id
    assert usage_row.project_name == "proj-a"
    assert usage_row.author == "Alice"
    assert usage_row.prompt_tokens == 20
    assert usage_row.completion_tokens == 10
    assert usage_row.total_tokens == 30
