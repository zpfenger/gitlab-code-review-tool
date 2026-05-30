"""efficiency_llm 测试 — 解析 LLM 输出"""
from unittest.mock import patch

from app.services.efficiency_llm import (
    parse_score, parse_work_summary, parse_review_summary,
    map_score_to_grade, build_user_prompt, call_and_parse,
    build_monthly_system_prompt, build_monthly_user_prompt,
    call_and_parse_monthly,
)


# ── 评分解析 ─────────────────────────────────────
def test_parse_score_with_full_text():
    text = "评分明细：xxx\n\n## 总分：85 分"
    assert parse_score(text) == 85


def test_parse_score_handles_chinese_colon():
    text = "总分：73分"
    assert parse_score(text) == 73


def test_parse_score_missing_returns_zero():
    assert parse_score("没有评分相关内容") == 0


def test_parse_score_empty():
    assert parse_score("") == 0


def test_parse_score_rejects_out_of_range():
    assert parse_score("总分：999 分") == 0


# ── 等级映射 ─────────────────────────────────────
def test_map_score_excellent():
    assert map_score_to_grade(95) == "优秀"
    assert map_score_to_grade(90) == "优秀"


def test_map_score_good():
    assert map_score_to_grade(89) == "良好"
    assert map_score_to_grade(75) == "良好"


def test_map_score_average():
    assert map_score_to_grade(74) == "一般"
    assert map_score_to_grade(60) == "一般"


def test_map_score_poor():
    assert map_score_to_grade(59) == "待改进"
    assert map_score_to_grade(0) == "待改进"


def test_map_score_none_returns_none():
    assert map_score_to_grade(None) is None


# ── 工作总结解析 ─────────────────────────────────
def test_parse_work_summary_extracts_list():
    text = """## 主要工作（不超过 5 条）
1. 实现登录功能
2. 修复购物车 bug
3. 重构订单服务
4. 补充单元测试
5. 优化慢查询

## 总分：85 分"""
    items = parse_work_summary(text)
    assert items == [
        "实现登录功能",
        "修复购物车 bug",
        "重构订单服务",
        "补充单元测试",
        "优化慢查询",
    ]


def test_parse_work_summary_dash_bullets():
    text = """## 主要工作
- 实现 A
- 修复 B"""
    items = parse_work_summary(text)
    assert items == ["实现 A", "修复 B"]


def test_parse_work_summary_caps_at_top_n():
    text = """## 主要工作
1. a
2. b
3. c
4. d
5. e
6. f
7. g"""
    items = parse_work_summary(text, top_n=3)
    assert items == ["a", "b", "c"]


def test_parse_work_summary_missing_returns_empty():
    assert parse_work_summary("没有这一块") == []


# ── 评分简述提取 ─────────────────────────────────
def test_parse_review_summary_takes_first_paragraph():
    text = """## 评分简述
代码质量良好，注释清晰，但存在 N+1 查询问题。

## 评分明细
..."""
    s = parse_review_summary(text)
    assert "代码质量良好" in s


def test_parse_review_summary_fallback_truncates():
    text = "x" * 500
    s = parse_review_summary(text)
    assert len(s) <= 200


# ── prompt 构造 ──────────────────────────────────
def test_build_user_prompt_contains_inputs():
    prompt = build_user_prompt(
        author_name="张三",
        commits_text="feat: add login\nfix: bug",
        diffs_text="+code line",
        top_n=5,
    )
    assert "张三" in prompt
    assert "feat: add login" in prompt
    assert "+code line" in prompt
    assert "5" in prompt


# ── call_and_parse 集成 ──────────────────────────
def test_call_and_parse_returns_failure_dict_when_llm_none():
    with patch("app.services.efficiency_llm.call_llm", return_value=None):
        result = call_and_parse(
            api_url="x", api_key="x", model="m",
            author_name="A", commits_text="", diffs_text="",
        )
    assert result["success"] is False
    assert result["score"] == 0
    assert result["grade"] is None
    assert result["work_summary"] == []
    assert result["review_summary"] == ""
    assert result["raw"] is None


def test_call_and_parse_returns_parsed_fields_on_success():
    fake_raw = """## 评分简述
代码质量优秀。

## 主要工作（不超过 5 条）
1. 实现 A
2. 修复 B

## 总分：92 分
"""
    with patch("app.services.efficiency_llm.call_llm", return_value=fake_raw):
        result = call_and_parse(
            api_url="x", api_key="x", model="m",
            author_name="A", commits_text="x", diffs_text="x",
        )
    assert result["success"] is True
    assert result["score"] == 92
    assert result["grade"] == "优秀"
    assert result["work_summary"] == ["实现 A", "修复 B"]
    assert "代码质量优秀" in result["review_summary"]


# ── 月度 LLM 测试 ──────────────────────────────────
def test_parse_monthly_score():
    """解析月度总分"""
    from app.services.efficiency_llm import parse_score
    text = "## 月度总分：82 分"
    assert parse_score(text) == 82


def test_parse_monthly_work_summary():
    """解析月度工作总结"""
    from app.services.efficiency_llm import parse_work_summary
    text = """## 月度主要工作（不超过 10 条）
1. 完成用户模块重构
2. 修复支付系统 Bug
3. 优化查询性能
"""
    result = parse_work_summary(text, top_n=10)
    assert len(result) == 3
    assert "完成用户模块重构" in result[0]


def test_parse_monthly_review_summary():
    """解析月度评分简述"""
    from app.services.efficiency_llm import parse_review_summary
    text = """## 月度评分简述
本月代码质量整体良好，完成了多个核心模块的重构工作。

## 月度主要工作
1. 重构
"""
    result = parse_review_summary(text)
    assert "本月代码质量整体良好" in result


# ── 月度 prompt 构造 ──────────────────────────────
def test_build_monthly_system_prompt_contains_fields():
    prompt = build_monthly_system_prompt(
        author_name="张三", year_month="2026-05", top_n=10,
    )
    assert "张三" in prompt
    assert "2026-05" in prompt
    assert "10" in prompt
    assert "月度评分简述" in prompt
    assert "月度总分" in prompt


def test_build_monthly_user_prompt_contains_inputs():
    prompt = build_monthly_user_prompt(
        author_name="李四", year_month="2026-04",
        active_days=20, commits_count=50,
        additions=1000, deletions=500,
        projects="project-a, project-b",
        daily_scores_summary="4/1: 85分\n4/2: 90分",
        top_n=10,
    )
    assert "李四" in prompt
    assert "2026-04" in prompt
    assert "20" in prompt
    assert "50" in prompt
    assert "1000" in prompt
    assert "500" in prompt
    assert "project-a, project-b" in prompt
    assert "85分" in prompt


# ── call_and_parse_monthly 集成 ────────────────────
def test_call_and_parse_monthly_returns_failure_dict_when_llm_none():
    with patch("app.services.efficiency_llm.call_monthly_llm", return_value=None):
        result = call_and_parse_monthly(
            api_url="x", api_key="x", model="m",
            author_name="A", year_month="2026-05",
            active_days=20, commits_count=50,
            additions=1000, deletions=500,
            projects="proj", daily_scores_summary="",
        )
    assert result["success"] is False
    assert result["score"] == 0
    assert result["grade"] is None
    assert result["work_summary"] == []
    assert result["review_summary"] == ""
    assert result["raw"] is None


def test_call_and_parse_monthly_returns_parsed_fields_on_success():
    fake_raw = """## 月度评分简述
本月代码质量整体良好。

## 月度主要工作（不超过 10 条）
1. 完成用户模块重构
2. 修复支付系统 Bug

## 月度总分：85 分
"""
    with patch("app.services.efficiency_llm.call_monthly_llm", return_value=fake_raw):
        result = call_and_parse_monthly(
            api_url="x", api_key="x", model="m",
            author_name="A", year_month="2026-05",
            active_days=20, commits_count=50,
            additions=1000, deletions=500,
            projects="proj", daily_scores_summary="",
        )
    assert result["success"] is True
    assert result["score"] == 85
    assert result["grade"] == "良好"
    assert result["work_summary"] == ["完成用户模块重构", "修复支付系统 Bug"]
    assert "本月代码质量整体良好" in result["review_summary"]
