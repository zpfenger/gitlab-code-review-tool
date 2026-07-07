# tests/test_services/test_gitlab_client.py
"""GitLab 客户端测试"""
import pytest
from unittest.mock import Mock, patch, MagicMock
from types import SimpleNamespace
import gitlab.exceptions
from app.services.gitlab_client import GitLabClient, GitLabAuthError, GitLabConnectionError


class TestGitLabClient:
    """GitLab 客户端测试类"""

    @pytest.fixture
    def mock_gitlab(self):
        """Mock GitLab 实例"""
        with patch('app.services.gitlab_client.gitlab.Gitlab') as mock:
            yield mock

    @pytest.fixture
    def client(self, mock_gitlab):
        """创建 GitLab 客户端实例"""
        return GitLabClient(
            gitlab_url="https://gitlab.example.com",
            access_token="test-token"
        )

    def test_init(self, mock_gitlab):
        """测试初始化"""
        client = GitLabClient(
            gitlab_url="https://gitlab.example.com",
            access_token="test-token"
        )
        mock_gitlab.assert_called_once_with(
            "https://gitlab.example.com",
            private_token="test-token",
            timeout=60,
        )

    def test_test_connection_success(self, client, mock_gitlab):
        """测试连接成功"""
        mock_gitlab.return_value.user.get.return_value = {"id": 1, "username": "test"}
        result = client.test_connection()
        assert result is True

    def test_test_connection_failure(self, client, mock_gitlab):
        """测试连接失败（通用异常返回 False）"""
        mock_gitlab.return_value.auth.side_effect = Exception("Connection failed")
        result = client.test_connection()
        assert result is False

    def test_test_connection_auth_error(self, client, mock_gitlab):
        """测试连接认证失败（401 抛出 GitLabAuthError）"""
        mock_gitlab.return_value.auth.side_effect = gitlab.exceptions.GitlabAuthenticationError(
            error_message="401 Unauthorized", response_code=401
        )
        with pytest.raises(GitLabAuthError) as exc_info:
            client.test_connection()
        assert "认证失败" in str(exc_info.value)

    def test_test_connection_network_error(self, client, mock_gitlab):
        """测试连接网络失败（抛出 GitLabConnectionError）"""
        mock_gitlab.return_value.auth.side_effect = gitlab.exceptions.GitlabConnectionError("Connection refused")
        with pytest.raises(GitLabConnectionError) as exc_info:
            client.test_connection()
        assert "无法连接" in str(exc_info.value)

    def test_get_branches(self, client, mock_gitlab):
        """测试获取分支列表"""
        mock_project = Mock()
        # 创建带有 commit 属性的 mock branch
        mock_branch1 = Mock()
        mock_branch1.name = "main"
        mock_branch1.commit = {"id": "abc123", "message": "init", "author_name": "user"}
        mock_branch1.protected = True
        mock_branch1.merged = True
        mock_branch1.default = True

        mock_branch2 = Mock()
        mock_branch2.name = "develop"
        mock_branch2.commit = {"id": "def456", "message": "update", "author_name": "user"}
        mock_branch2.protected = False
        mock_branch2.merged = False
        mock_branch2.default = False

        mock_branch3 = Mock()
        mock_branch3.name = "feature/test"
        mock_branch3.commit = {"id": "ghi789", "message": "feature", "author_name": "user"}
        mock_branch3.protected = False
        mock_branch3.merged = False
        mock_branch3.default = False

        mock_project.branches.list.return_value = [mock_branch1, mock_branch2, mock_branch3]
        mock_gitlab.return_value.projects.get.return_value = mock_project

        branches = client.get_branches(project_id=1)

        assert len(branches) == 3
        assert branches[0]["name"] == "main"
        assert branches[1]["name"] == "develop"
        assert branches[2]["name"] == "feature/test"

    def test_get_branches_auth_error(self, client, mock_gitlab):
        """测试获取分支列表时认证失败"""
        mock_project = Mock()
        mock_project.branches.list.side_effect = gitlab.exceptions.GitlabAuthenticationError(
            error_message="401 Unauthorized", response_code=401
        )
        mock_gitlab.return_value.projects.get.return_value = mock_project

        with pytest.raises(GitLabAuthError) as exc_info:
            client.get_branches(project_id=1)
        assert "认证失败" in str(exc_info.value)
        assert exc_info.value.project_id == 1

    def test_get_branches_generic_error(self, client, mock_gitlab):
        """测试获取分支列表时通用异常返回空列表"""
        mock_project = Mock()
        mock_project.branches.list.side_effect = Exception("Network timeout")
        mock_gitlab.return_value.projects.get.return_value = mock_project

        branches = client.get_branches(project_id=1)
        assert branches == []

    def test_get_commits(self, client, mock_gitlab):
        """测试获取提交列表"""
        mock_project = Mock()
        mock_commits = [
            Mock(
                id="abc123",
                short_id="abc123",
                title="Test commit",
                author_name="Test User",
                author_email="test@example.com",
                created_at="2024-01-01T00:00:00Z",
                message="Test commit message",
                parent_ids=[]
            )
        ]
        mock_project.commits.list.return_value = mock_commits
        mock_gitlab.return_value.projects.get.return_value = mock_project

        commits = client.get_commits(
            project_id=1,
            since="2024-01-01T00:00:00Z",
            until="2024-01-02T00:00:00Z",
            ref_name="main"
        )

        assert len(commits) == 1
        assert commits[0]["id"] == "abc123"
        assert commits[0]["title"] == "Test commit"

    def test_get_commits_with_pagination(self, client, mock_gitlab):
        """测试获取提交列表（分页）"""
        mock_project = Mock()
        mock_commits = [
            Mock(
                id=f"commit{i}",
                short_id=f"commit{i}",
                title=f"Commit {i}",
                author_name="Test User",
                author_email="test@example.com",
                created_at="2024-01-01T00:00:00Z",
                message=f"Commit {i}",
                parent_ids=[]
            )
            for i in range(5)
        ]
        mock_project.commits.list.return_value = mock_commits
        mock_gitlab.return_value.projects.get.return_value = mock_project

        commits = client.get_commits(
            project_id=1,
            since="2024-01-01T00:00:00Z",
            until="2024-01-02T00:00:00Z",
            ref_name="main",
            per_page=5
        )

        assert len(commits) == 5

    def test_get_commits_no_page_kwarg(self, client, mock_gitlab):
        """回归测试：commits.list 不能同时携带 page 和 get_all=True

        python-gitlab 翻页时会将 page kwarg 重新注入"下一页"请求，
        导致结果超过一页时无限循环拉取第 1 页。
        """
        mock_project = Mock()
        mock_project.commits.list.return_value = []
        mock_gitlab.return_value.projects.get.return_value = mock_project

        client.get_commits(project_id=1, ref_name="dev")

        _, called_kwargs = mock_project.commits.list.call_args
        assert "page" not in called_kwargs
        assert called_kwargs["get_all"] is True

    def test_get_commit_diff(self, client, mock_gitlab):
        """测试获取提交差异"""
        mock_project = Mock()
        mock_commit = Mock()
        mock_commit.diff.return_value = [
            {
                "diff": "@@ -1,5 +1,5 @@\n-old\n+new\n",
                "new_path": "test.py",
                "old_path": "test.py",
                "new_file": False,
                "deleted_file": False,
                "renamed_file": False
            }
        ]
        mock_project.commits.get.return_value = mock_commit
        mock_gitlab.return_value.projects.get.return_value = mock_project

        diffs = client.get_commit_diff(project_id=1, commit_sha="abc123")

        assert len(diffs) == 1
        assert diffs[0]["new_path"] == "test.py"
        assert diffs[0]["diff"].startswith("@@")

    def test_get_file_content(self, client, mock_gitlab):
        """测试获取文件内容"""
        mock_project = Mock()
        mock_file = Mock()
        mock_file.decode.return_value = "# Test File\nprint('hello')"
        mock_project.files.get.return_value = mock_file
        mock_gitlab.return_value.projects.get.return_value = mock_project

        content = client.get_file_content(
            project_id=1,
            file_path="README.md",
            ref="main"
        )

        assert "Test File" in content

    def test_get_file_content_not_found(self, client, mock_gitlab):
        """测试获取不存在的文件"""
        mock_project = Mock()
        mock_project.files.get.side_effect = Exception("File not found")
        mock_gitlab.return_value.projects.get.return_value = mock_project

        content = client.get_file_content(
            project_id=1,
            file_path="nonexistent.md",
            ref="main"
        )

        assert content is None

    def test_get_project_info(self, client, mock_gitlab):
        """测试获取项目信息"""
        mock_project = Mock()
        mock_project.name = "Test Project"
        mock_project.path_with_namespace = "group/test-project"
        mock_project.web_url = "https://gitlab.example.com/group/test-project"
        mock_gitlab.return_value.projects.get.return_value = mock_project

        info = client.get_project_info(project_id=1)

        assert info["name"] == "Test Project"
        assert info["path"] == "group/test-project"

    def test_list_accessible_projects(self, client, mock_gitlab):
        """测试列出 Access Token 可访问的项目"""
        mock_project1 = Mock()
        mock_project1.id = 101
        mock_project1.name = "Alpha"
        mock_project1.path_with_namespace = "group/alpha"
        mock_project1.description = "Alpha project"
        mock_project1.web_url = "https://gitlab.example.com/group/alpha"
        mock_project1.default_branch = "main"

        mock_project2 = Mock()
        mock_project2.id = 102
        mock_project2.name = "Beta"
        mock_project2.path_with_namespace = "group/sub/beta"
        mock_project2.description = None
        mock_project2.web_url = "https://gitlab.example.com/group/sub/beta"
        mock_project2.default_branch = None

        mock_gitlab.return_value.projects.list.return_value = [mock_project1, mock_project2]

        projects = client.list_accessible_projects()

        mock_gitlab.return_value.projects.list.assert_called_once_with(get_all=True, simple=True)
        assert projects == [
            {
                "id": 101,
                "name": "Alpha",
                "path_with_namespace": "group/alpha",
                "description": "Alpha project",
                "web_url": "https://gitlab.example.com/group/alpha",
                "default_branch": "main",
            },
            {
                "id": 102,
                "name": "Beta",
                "path_with_namespace": "group/sub/beta",
                "description": "",
                "web_url": "https://gitlab.example.com/group/sub/beta",
                "default_branch": "",
            },
        ]

    def test_list_accessible_projects_auth_error(self, client, mock_gitlab):
        """测试列出项目时认证失败"""
        mock_gitlab.return_value.projects.list.side_effect = gitlab.exceptions.GitlabAuthenticationError(
            error_message="401 Unauthorized", response_code=401
        )

        with pytest.raises(GitLabAuthError) as exc_info:
            client.list_accessible_projects()

        assert "认证失败" in str(exc_info.value)

    def test_list_accessible_projects_connection_error(self, client, mock_gitlab):
        """测试列出项目时 GitLab 连接失败"""
        mock_gitlab.return_value.projects.list.side_effect = gitlab.exceptions.GitlabConnectionError(
            "Connection refused"
        )

        with pytest.raises(GitLabConnectionError) as exc_info:
            client.list_accessible_projects()

        assert "无法连接" in str(exc_info.value)

    def test_get_project_members_fetches_email_from_user_detail_when_member_email_missing(
        self, client, mock_gitlab
    ):
        """成员列表无 email 时，从用户详情补齐邮箱"""
        mock_project = Mock()
        direct_member = SimpleNamespace(id=21)
        inherited_member = SimpleNamespace(
            id=21,
            username="jane",
            name="Jane Doe",
            access_level=30,
            source={"type": "project"},
        )
        mock_project.members.list.return_value = [direct_member]
        mock_project.members_all.list.return_value = [inherited_member]
        mock_gitlab.return_value.projects.get.return_value = mock_project
        mock_gitlab.return_value.users.get.return_value = SimpleNamespace(
            email="jane@example.com",
            public_email="",
            state="active",
            bot=False,
            user_type="human",
        )

        members = client.get_project_members(project_id=1)

        assert members[0]["email"] == "jane@example.com"
        assert members[0]["state"] == "active"
        assert members[0]["bot"] is False
        assert members[0]["user_type"] == "human"
        mock_gitlab.return_value.users.get.assert_called_once_with(21)

    def test_get_project_members_uses_public_email_from_user_detail(
        self, client, mock_gitlab
    ):
        """用户详情无 email 时，使用 public_email 兜底"""
        mock_project = Mock()
        member = SimpleNamespace(
            id=22,
            username="public",
            name="Public User",
            access_level=30,
            source={"type": "project"},
        )
        mock_project.members.list.return_value = [SimpleNamespace(id=22)]
        mock_project.members_all.list.return_value = [member]
        mock_gitlab.return_value.projects.get.return_value = mock_project
        mock_gitlab.return_value.users.get.return_value = SimpleNamespace(
            email="",
            public_email="public@example.com",
        )

        members = client.get_project_members(project_id=1)

        assert members[0]["email"] == "public@example.com"

    def test_get_project_members_includes_inactive_and_bot_metadata_from_user_detail(
        self, client, mock_gitlab
    ):
        """同步策略需要用户状态和 Bot 标记"""
        mock_project = Mock()
        inactive_member = SimpleNamespace(
            id=23,
            username="blocked",
            name="Blocked User",
            access_level=30,
            source={"type": "project"},
        )
        bot_member = SimpleNamespace(
            id=24,
            username="project_1_bot",
            name="Project Bot",
            access_level=30,
            source={"type": "project"},
        )
        mock_project.members.list.return_value = [
            SimpleNamespace(id=23),
            SimpleNamespace(id=24),
        ]
        mock_project.members_all.list.return_value = [inactive_member, bot_member]
        mock_gitlab.return_value.projects.get.return_value = mock_project
        mock_gitlab.return_value.users.get.side_effect = [
            SimpleNamespace(
                email="blocked@example.com",
                state="blocked",
                bot=False,
                user_type="human",
            ),
            SimpleNamespace(
                email="project_1_bot@example.com",
                state="active",
                bot=True,
                user_type="project_bot",
            ),
        ]

        members = client.get_project_members(project_id=1)

        assert members[0]["state"] == "blocked"
        assert members[0]["bot"] is False
        assert members[1]["state"] == "active"
        assert members[1]["bot"] is True
        assert members[1]["user_type"] == "project_bot"
