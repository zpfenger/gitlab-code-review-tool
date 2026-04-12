# tests/test_services/test_svn_uploader.py
"""SVN 上传服务测试"""
import pytest
from unittest.mock import Mock, patch, MagicMock
import subprocess
from app.services.svn_uploader import SVNUploader


class TestSVNUploader:
    """SVN 上传服务测试类"""

    @pytest.fixture
    def uploader(self):
        """创建 SVN 上传器实例"""
        return SVNUploader(
            svn_url="https://svn.example.com/repo",
            username="testuser",
            password="testpass"
        )

    @pytest.fixture
    def mock_subprocess(self):
        """Mock subprocess.run"""
        with patch('app.services.svn_uploader.subprocess.run') as mock:
            yield mock

    def test_init(self, uploader):
        """测试初始化"""
        assert uploader.svn_url == "https://svn.example.com/repo"
        assert uploader.username == "testuser"
        assert uploader.password == "testpass"

    def test_test_connection_success(self, uploader, mock_subprocess):
        """测试连接成功"""
        mock_subprocess.return_value = Mock(
            returncode=0,
            stdout="Repository Root: https://svn.example.com/repo"
        )

        result = uploader.test_connection()

        assert result is True
        mock_subprocess.assert_called_once()

    def test_test_connection_failure(self, uploader, mock_subprocess):
        """测试连接失败"""
        mock_subprocess.return_value = Mock(
            returncode=1,
            stderr="Connection refused"
        )

        result = uploader.test_connection()

        assert result is False

    def test_upload_file_success(self, uploader, mock_subprocess, tmp_path):
        """测试上传文件成功"""
        # 创建测试文件
        test_file = tmp_path / "test.md"
        test_file.write_text("Test content")

        mock_subprocess.return_value = Mock(
            returncode=0,
            stdout="Commit succeeded"
        )

        result = uploader.upload_file(
            local_path=str(test_file),
            remote_path="/reports/test.md",
            commit_message="Test commit"
        )

        assert result is True

    def test_upload_file_not_found(self, uploader):
        """测试上传不存在的文件"""
        result = uploader.upload_file(
            local_path="/nonexistent/file.md",
            remote_path="/reports/file.md",
            commit_message="Test"
        )

        assert result is False

    def test_upload_directory_success(self, uploader, mock_subprocess, tmp_path):
        """测试上传目录成功"""
        # 创建测试目录
        test_dir = tmp_path / "reports"
        test_dir.mkdir()
        (test_dir / "file1.md").write_text("Content 1")
        (test_dir / "file2.md").write_text("Content 2")

        mock_subprocess.return_value = Mock(
            returncode=0,
            stdout="Commit succeeded"
        )

        result = uploader.upload_directory(
            local_path=str(test_dir),
            remote_path="/reports",
            commit_message="Upload reports"
        )

        assert result is True

    def test_create_directory(self, uploader, mock_subprocess):
        """测试创建远程目录"""
        mock_subprocess.return_value = Mock(returncode=0)

        result = uploader.create_directory("/reports/new_folder")

        assert result is True

    def test_list_files(self, uploader, mock_subprocess):
        """测试列出文件"""
        mock_subprocess.return_value = Mock(
            returncode=0,
            stdout="file1.md\nfile2.md\nfile3.md"
        )

        files = uploader.list_files("/reports")

        assert len(files) == 3
        assert "file1.md" in files

    def test_list_files_empty(self, uploader, mock_subprocess):
        """测试列出空目录"""
        mock_subprocess.return_value = Mock(
            returncode=0,
            stdout=""
        )

        files = uploader.list_files("/empty_folder")

        assert len(files) == 0

    def test_delete_file(self, uploader, mock_subprocess):
        """测试删除文件"""
        mock_subprocess.return_value = Mock(returncode=0)

        result = uploader.delete_file(
            remote_path="/reports/old.md",
            commit_message="Delete old report"
        )

        assert result is True

    def test_get_file_info(self, uploader, mock_subprocess):
        """测试获取文件信息"""
        mock_subprocess.return_value = Mock(
            returncode=0,
            stdout="""Path: test.md
Name: test.md
URL: https://svn.example.com/repo/test.md
Repository Root: https://svn.example.com/repo
Revision: 123
"""
        )

        info = uploader.get_file_info("/reports/test.md")

        assert info is not None
        assert info["Name"] == "test.md"
        assert info["Revision"] == "123"

    def test_svn_command_timeout(self, uploader, mock_subprocess):
        """测试 SVN 命令超时"""
        mock_subprocess.side_effect = subprocess.TimeoutExpired(
            cmd="svn",
            timeout=30
        )

        result = uploader.test_connection()

        assert result is False

    def test_svn_command_error(self, uploader, mock_subprocess):
        """测试 SVN 命令错误"""
        mock_subprocess.side_effect = subprocess.CalledProcessError(
            returncode=1,
            cmd="svn",
            stderr="Error occurred"
        )

        result = uploader.test_connection()

        assert result is False

    def test_build_svn_command(self, uploader):
        """测试构建 SVN 命令"""
        cmd = uploader._build_command("info", ["https://svn.example.com/repo"])

        assert "svn" in cmd
        assert "info" in cmd
        assert "--username" in cmd
        assert "testuser" in cmd

    def test_svn_checkout(self, uploader, mock_subprocess, tmp_path):
        """测试 SVN 检出"""
        mock_subprocess.return_value = Mock(returncode=0)

        result = uploader.checkout(
            remote_path="/reports",
            local_path=str(tmp_path / "checkout")
        )

        assert result is True

    def test_svn_update(self, uploader, mock_subprocess, tmp_path):
        """测试 SVN 更新"""
        mock_subprocess.return_value = Mock(returncode=0)

        result = uploader.update(str(tmp_path))

        assert result is True

    def test_upload_with_retry(self, uploader, mock_subprocess, tmp_path):
        """测试带重试的上传"""
        test_file = tmp_path / "test.md"
        test_file.write_text("Test")

        # 模拟多次调用：info 检查失败，然后成功
        # upload_file 会先调用 info，然后调用 import
        mock_subprocess.side_effect = [
            Mock(returncode=1, stderr="Temporary error"),  # info 检查失败
            Mock(returncode=1, stderr="Temporary error"),  # import 失败（第一次尝试）
            Mock(returncode=0, stdout="Committed revision 1"),  # info 检查成功（第二次尝试）
            Mock(returncode=0, stdout="Committed revision 2"),  # import 成功
        ]

        uploader.max_retries = 3
        uploader.retry_delay = 0.01

        result = uploader.upload_file(
            local_path=str(test_file),
            remote_path="/reports/test.md",
            commit_message="Test"
        )

        assert result is True
        assert mock_subprocess.call_count >= 2
