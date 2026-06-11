"""efficiency_prompt_template 测试 — 模板格式化与内容验证"""
from app.services.efficiency_prompt_template import (
    EFFICIENCY_STANDARD_TEMPLATE,
    EFFICIENCY_MONTHLY_STANDARD_TEMPLATE,
    get_efficiency_template,
    get_monthly_template,
)


# ── 日度模板测试 ─────────────────────────────────────
def test_standard_template_contains_author_name():
    """模板包含员工姓名"""
    result = EFFICIENCY_STANDARD_TEMPLATE.format(author_name="张三", top_n=5)
    assert "张三" in result


def test_standard_template_contains_scoring_dimensions():
    """模板包含所有评分维度"""
    result = EFFICIENCY_STANDARD_TEMPLATE.format(author_name="测试", top_n=5)
    assert "注释质量" in result
    assert "业务逻辑校验" in result
    assert "性能优化" in result
    assert "安全风险" in result
    assert "代码架构" in result
    assert "编码规范" in result


def test_standard_template_contains_score_ranges():
    """模板包含分数段说明"""
    result = EFFICIENCY_STANDARD_TEMPLATE.format(author_name="测试", top_n=5)
    assert "5 分" in result
    assert "30 分" in result
    assert "40 分" in result
    assert "10 分" in result


def test_standard_template_contains_output_format():
    """模板包含输出格式要求"""
    result = EFFICIENCY_STANDARD_TEMPLATE.format(author_name="测试", top_n=5)
    assert "评分简述" in result
    assert "评分明细" in result
    assert "主要工作" in result
    assert "总分" in result


def test_standard_template_contains_check_items():
    """模板包含检查项"""
    result = EFFICIENCY_STANDARD_TEMPLATE.format(author_name="测试", top_n=5)
    assert "检查项" in result
    assert "SQL 注入" in result
    assert "XSS" in result


def test_get_efficiency_template_default():
    """使用默认模板"""
    result = get_efficiency_template(author_name="李四", top_n=3)
    assert "李四" in result
    assert "3" in result
    assert "评分简述" in result


def test_get_efficiency_template_custom_appends_to_standard():
    """自定义内容作为补充要求追加，标准评分锚点保留"""
    custom = "自定义 {author_name} {top_n}"
    result = get_efficiency_template(author_name="王五", top_n=10, custom_template=custom)
    assert "王五" in result
    assert "10" in result
    assert "自定义" in result
    # 标准模板锚点必须仍然存在（不被自定义内容覆盖）
    assert "评分明细" in result
    assert "业务逻辑校验" in result
    assert "补充评分要求" in result
    # 标准内容在前，自定义内容在后
    assert result.index("评分明细") < result.index("自定义")


def test_get_efficiency_template_custom_with_braces_does_not_crash():
    """自定义内容含代码大括号时不应抛 format 异常"""
    custom = "重点检查 if (x) { return; } 这类写法，员工：{author_name}"
    result = get_efficiency_template(author_name="赵六", top_n=5, custom_template=custom)
    assert "赵六" in result
    assert "{ return; }" in result


def test_get_efficiency_template_blank_custom_uses_standard_only():
    """自定义内容为空白时不追加补充段"""
    result = get_efficiency_template(author_name="李四", top_n=5, custom_template="   ")
    assert "补充评分要求" not in result


def test_standard_template_uses_deduction_scoring():
    """模板采用扣分制规则"""
    result = get_efficiency_template(author_name="测试", top_n=5)
    assert "扣分制" in result
    assert "从满分起步" in result


# ── 月度模板测试 ─────────────────────────────────────
def test_monthly_template_contains_fields():
    """月度模板包含必要字段"""
    result = EFFICIENCY_MONTHLY_STANDARD_TEMPLATE.format(
        author_name="张三", year_month="2026-05", top_n=10,
    )
    assert "张三" in result
    assert "2026-05" in result
    assert "月度评分简述" in result
    assert "月度总分" in result


def test_monthly_template_contains_scoring_dimensions():
    """月度模板包含所有评分维度"""
    result = EFFICIENCY_MONTHLY_STANDARD_TEMPLATE.format(
        author_name="测试", year_month="2026-01", top_n=10,
    )
    assert "注释质量" in result
    assert "业务逻辑校验" in result
    assert "性能优化" in result
    assert "安全风险" in result
    assert "代码架构" in result
    assert "编码规范" in result


def test_monthly_template_contains_score_table():
    """月度模板包含评分标准表"""
    result = EFFICIENCY_MONTHLY_STANDARD_TEMPLATE.format(
        author_name="测试", year_month="2026-01", top_n=10,
    )
    assert "| 分数 | 标准 |" in result
    assert "优秀" in result
    assert "良好" in result
    assert "一般" in result
    assert "待改进" in result


def test_get_monthly_template_default():
    """使用默认月度模板"""
    result = get_monthly_template(
        author_name="李四", year_month="2026-06", top_n=5,
    )
    assert "李四" in result
    assert "2026-06" in result
    assert "5" in result
    assert "月度评分简述" in result


def test_get_monthly_template_custom_appends_to_standard():
    """月度自定义内容作为补充要求追加，标准锚点保留"""
    custom = "自定义月度 {author_name} {year_month} {top_n}"
    result = get_monthly_template(
        author_name="王五", year_month="2026-07", top_n=8,
        custom_template=custom,
    )
    assert "王五" in result
    assert "2026-07" in result
    assert "8" in result
    assert "自定义月度" in result
    # 标准模板锚点必须仍然存在
    assert "月度评分明细" in result
    assert "月度总分" in result
    assert "补充评分要求" in result
