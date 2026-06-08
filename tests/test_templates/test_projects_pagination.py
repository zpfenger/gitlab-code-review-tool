from pathlib import Path


def test_projects_template_defines_client_side_pagination():
    """项目管理页应有真正的客户端分页函数和可更新的分页容器。"""
    template = Path("app/templates/projects.html").read_text(encoding="utf-8")

    assert 'id="paginationInfo"' in template
    assert 'id="paginationControls"' in template
    assert 'id="pageJumpInput"' in template
    assert "function applyProjectFilters()" in template
    assert "function renderProjectsPage()" in template
    assert "function changePageSize()" in template
    assert "function goProjectsPage(page)" in template
