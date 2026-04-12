# tests/test_services/test_stats_generator.py
"""代码统计服务测试"""
import pytest
from app.services.stats_generator import StatsGenerator


class TestStatsGenerator:
    """代码统计服务测试类"""

    @pytest.fixture
    def generator(self):
        """创建统计生成器实例"""
        return StatsGenerator()

    @pytest.fixture
    def sample_diffs(self):
        """示例差异列表"""
        return [
            {
                "diff": """@@ -1,5 +1,6 @@
 def hello():
-    print("hello")
+    print("hello world")
+    # added comment
     return True
""",
                "new_path": "src/main.py",
                "old_path": "src/main.py",
                "new_file": False,
                "deleted_file": False
            },
            {
                "diff": """@@ -0,0 +1,10 @@
+# New file
+def new_function():
+    pass
+    pass
+    pass
+    pass
+    pass
+    pass
+    pass
+    pass
""",
                "new_path": "src/utils.py",
                "old_path": "/dev/null",
                "new_file": True,
                "deleted_file": False
            },
            {
                "diff": """@@ -1,3 +0,0 @@
-old line 1
-old line 2
-old line 3
""",
                "new_path": "/dev/null",
                "old_path": "src/old.py",
                "new_file": False,
                "deleted_file": True
            }
        ]

    def test_init(self, generator):
        """测试初始化"""
        assert generator is not None

    def test_calculate_diff_stats(self, generator, sample_diffs):
        """测试计算差异统计"""
        stats = generator.calculate_diff_stats(sample_diffs)

        assert "total_additions" in stats
        assert "total_deletions" in stats
        assert "files_changed" in stats
        assert stats["files_changed"] == 3
        assert stats["total_additions"] > 0
        assert stats["total_deletions"] > 0

    def test_calculate_file_stats(self, generator, sample_diffs):
        """测试计算文件统计"""
        file_stats = generator.calculate_file_stats(sample_diffs)

        assert len(file_stats) == 3
        # 检查第一个文件
        main_py_stats = next((f for f in file_stats if f["path"] == "src/main.py"), None)
        assert main_py_stats is not None
        assert main_py_stats["additions"] == 2
        assert main_py_stats["deletions"] == 1
        assert main_py_stats["extension"] == ".py"

    def test_generate_extension_stats(self, generator, sample_diffs):
        """测试生成扩展名统计"""
        ext_stats = generator.generate_extension_stats(sample_diffs)

        assert ".py" in ext_stats
        assert ext_stats[".py"]["files"] == 3
        assert ext_stats[".py"]["additions"] > 0
        assert ext_stats[".py"]["deletions"] > 0

    def test_generate_summary_report(self, generator, sample_diffs):
        """测试生成摘要报告"""
        report = generator.generate_summary_report(sample_diffs)

        assert "代码变更统计" in report
        assert "文件统计" in report
        assert "扩展名统计" in report
        assert ".py" in report

    def test_parse_diff_lines(self, generator):
        """测试解析差异行"""
        diff = """@@ -1,3 +1,3 @@
-old line
+new line
 unchanged
-another old
+another new"""

        additions, deletions = generator._parse_diff_lines(diff)

        assert additions == 2
        assert deletions == 2

    def test_parse_diff_empty(self, generator):
        """测试解析空差异"""
        additions, deletions = generator._parse_diff_lines("")

        assert additions == 0
        assert deletions == 0

    def test_get_file_extension(self, generator):
        """测试获取文件扩展名"""
        assert generator._get_file_extension("test.py") == ".py"
        assert generator._get_file_extension("test.js") == ".js"
        assert generator._get_file_extension("test") == ""
        assert generator._get_file_extension("test.TEST") == ".test"

    def test_format_number(self, generator):
        """测试格式化数字"""
        assert generator._format_number(0) == "0"
        assert generator._format_number(100) == "100"
        assert generator._format_number(1000) == "1,000"
        assert generator._format_number(1234567) == "1,234,567"

    def test_empty_diffs(self, generator):
        """测试空差异列表"""
        stats = generator.calculate_diff_stats([])
        assert stats["total_additions"] == 0
        assert stats["total_deletions"] == 0
        assert stats["files_changed"] == 0

    def test_mixed_extensions(self, generator):
        """测试混合扩展名"""
        diffs = [
            {
                "diff": "+new line\n-old line",
                "new_path": "test.py",
                "old_path": "test.py",
                "new_file": False,
                "deleted_file": False
            },
            {
                "diff": "+new line\n-old line",
                "new_path": "test.js",
                "old_path": "test.js",
                "new_file": False,
                "deleted_file": False
            },
            {
                "diff": "+new line",
                "new_path": "README.md",
                "old_path": "README.md",
                "new_file": False,
                "deleted_file": False
            }
        ]

        ext_stats = generator.generate_extension_stats(diffs)

        assert ".py" in ext_stats
        assert ".js" in ext_stats
        assert ".md" in ext_stats

    def test_generate_detailed_report(self, generator, sample_diffs):
        """测试生成详细报告"""
        commit_info = {
            "id": "abc123",
            "title": "Test commit",
            "author_name": "Test User"
        }

        report = generator.generate_detailed_report(
            diffs=sample_diffs,
            commit_info=commit_info
        )

        assert "abc123" in report
        assert "Test commit" in report
        assert "Test User" in report
        assert "新增" in report or "删除" in report
