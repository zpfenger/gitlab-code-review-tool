from pathlib import Path


def test_settings_template_exposes_gitlab_sync_controls():
    template = Path("app/templates/settings.html").read_text(encoding="utf-8")

    assert 'id="gitlab_sync_enabled"' in template
    assert 'name="gitlab_sync_enabled"' in template
    assert 'id="gitlab_sync_schedule_time"' in template
    assert 'name="gitlab_sync_schedule_time"' in template
    assert 'id="gitlab_sync_default_password"' in template
    assert 'name="gitlab_sync_default_password"' in template
    assert "gitlab_sync_enabled:" in template
    assert "gitlab_sync_schedule_time:" in template
    assert "gitlab_sync_default_password:" in template


def test_projects_template_hides_operation_column_for_plain_users():
    template = Path("app/templates/projects.html").read_text(encoding="utf-8")

    assert "{% if show_project_actions %}" in template
    assert "{% if project.can_write %}" in template
    assert "colspan=\"{{ 8 if show_project_actions else 7 }}\"" in template


def test_roles_template_keeps_normal_user_out_of_role_cards():
    template = Path("app/templates/roles.html").read_text(encoding="utf-8")

    assert "有效角色数" in template
    assert "普通用户（无角色）" in template
    assert "roleDefinitions.map" in template
    assert '"normal_user"' not in template


def test_users_template_exposes_manual_gitlab_account_sync():
    template = Path("app/templates/users.html").read_text(encoding="utf-8")

    assert 'id="syncGitlabAccountsBtn"' in template
    assert "syncGitlabAccounts()" in template
    assert "/api/users/sync-gitlab" in template
    assert "loadUsers();" in template


def test_users_template_exposes_batch_delete_controls():
    template = Path("app/templates/users.html").read_text(encoding="utf-8")

    assert 'id="selectAllUsersCheckbox"' in template
    assert 'class="user-select-checkbox"' in template
    assert 'id="batchDeleteUsersBtn"' in template
    assert "batchDeleteSelectedUsers()" in template
    assert "updateBatchDeleteButton()" in template
    assert "/api/users/batch-delete" in template
