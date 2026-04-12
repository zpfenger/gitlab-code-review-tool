# app/services/gitlab_client.py
"""GitLab API 客户端服务"""
import gitlab
from typing import List, Dict, Optional, Any
from loguru import logger
from datetime import datetime


class GitLabClient:
    """GitLab API 客户端"""

    def __init__(self, gitlab_url: str, access_token: str):
        """
        初始化 GitLab 客户端

        Args:
            gitlab_url: GitLab 服务器地址
            access_token: 访问令牌
        """
        self.gitlab_url = gitlab_url
        self.access_token = access_token
        self.client = gitlab.Gitlab(gitlab_url, private_token=access_token)

    def test_connection(self) -> bool:
        """
        测试连接是否正常

        Returns:
            bool: 连接成功返回 True，否则返回 False
        """
        try:
            self.client.auth()
            return True
        except Exception as e:
            logger.error(f"GitLab 连接测试失败: {e}")
            return False

    def get_project_info(self, project_id: int) -> Dict[str, Any]:
        """
        获取项目信息

        Args:
            project_id: 项目 ID

        Returns:
            Dict: 项目信息
        """
        try:
            project = self.client.projects.get(project_id)
            return {
                "name": project.name,
                "path": project.path_with_namespace,
                "web_url": project.web_url,
                "description": getattr(project, 'description', ''),
                "default_branch": getattr(project, 'default_branch', 'main')
            }
        except Exception as e:
            logger.error(f"获取项目信息失败: {e}")
            return {}

    def get_branches(
        self,
        project_id: int,
        search: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        获取项目分支列表

        Args:
            project_id: 项目 ID
            search: 搜索关键词（可选）

        Returns:
            List[Dict]: 分支列表
        """
        try:
            project = self.client.projects.get(project_id)
            kwargs = {"all": True}
            if search:
                kwargs["search"] = search

            branches = project.branches.list(**kwargs)
            return [
                {
                    "name": branch.name,
                    "commit": {
                        "id": branch.commit["id"],
                        "message": branch.commit.get("message", ""),
                        "author_name": branch.commit.get("author_name", "")
                    },
                    "protected": getattr(branch, 'protected', False),
                    "merged": getattr(branch, 'merged', False),
                    "default": getattr(branch, 'default', False)
                }
                for branch in branches
            ]
        except Exception as e:
            logger.error(f"获取分支列表失败: {e}")
            return []

    def get_commits(
        self,
        project_id: int,
        since: Optional[str] = None,
        until: Optional[str] = None,
        ref_name: Optional[str] = None,
        author: Optional[str] = None,
        per_page: int = 100,
        page: int = 1,
        exclude_merge_commits: bool = True
    ) -> List[Dict[str, Any]]:
        """
        获取提交列表

        Args:
            project_id: 项目 ID
            since: 起始时间（ISO 8601 格式）
            until: 结束时间（ISO 8601 格式）
            ref_name: 分支或标签名
            author: 作者邮箱或用户名
            per_page: 每页数量
            page: 页码
            exclude_merge_commits: 是否排除合并提交（默认 True）

        Returns:
            List[Dict]: 提交列表（不含合并提交）
        """
        try:
            project = self.client.projects.get(project_id)
            kwargs = {"per_page": per_page, "page": page, "get_all": True}

            if since:
                kwargs["since"] = since
            if until:
                kwargs["until"] = until
            if ref_name:
                kwargs["ref_name"] = ref_name
            if author:
                kwargs["author"] = author
            # GitLab API 支持 with_stats 参数（获取统计信息）
            kwargs["with_stats"] = True

            commits = project.commits.list(**kwargs)
            result = []
            for commit in commits:
                parent_ids = getattr(commit, 'parent_ids', [])
                # 合并提交有 2 个或更多父提交，按提交人审查时跳过合并提交
                if exclude_merge_commits and len(parent_ids) >= 2:
                    logger.debug(
                        f"跳过合并提交: {commit.short_id} - {commit.title[:50]}"
                    )
                    continue
                result.append({
                    "id": commit.id,
                    "short_id": commit.short_id,
                    "title": commit.title,
                    "message": commit.message,
                    "author_name": commit.author_name,
                    "author_email": commit.author_email,
                    "created_at": commit.created_at,
                    "web_url": commit.web_url,
                    "parent_ids": parent_ids,
                    "is_merge_commit": len(parent_ids) >= 2,
                    "stats": getattr(commit, 'stats', {})
                })
            return result
        except Exception as e:
            logger.error(f"获取提交列表失败: {e}")
            return []

    def get_commit_diff(
        self,
        project_id: int,
        commit_sha: str
    ) -> Optional[List[Dict[str, Any]]]:
        """
        获取提交差异

        Args:
            project_id: 项目 ID
            commit_sha: 提交 SHA

        Returns:
            Optional[List[Dict]]: 差异列表，获取失败返回 None
        """
        try:
            project = self.client.projects.get(project_id)
            commit = project.commits.get(commit_sha)
            diffs_raw = commit.diff()

            # commit.diff() 可能返回 None（某些 GitLab 版本或权限配置下）
            if diffs_raw is None:
                logger.warning(
                    f"commit.diff() 返回 None, project_id={project_id}, "
                    f"sha={commit_sha[:8]}, "
                    f"可能是 Token 权限不足（需要 read_repository scope）"
                )
                return None

            # python-gitlab 的 diff() 可能返回 RESTObject 或 dict
            # 统一提取为 dict
            result = []
            for d in diffs_raw:
                if isinstance(d, dict):
                    diff_content = d.get("diff", "")
                    new_path = d.get("new_path", "")
                    old_path = d.get("old_path", "")
                else:
                    # RESTObject: 优先属性访问，回退 dict 访问
                    diff_content = getattr(d, "diff", None) or (d.get("diff", "") if hasattr(d, "get") else "")
                    new_path = getattr(d, "new_path", None) or (d.get("new_path", "") if hasattr(d, "get") else "")
                    old_path = getattr(d, "old_path", None) or (d.get("old_path", "") if hasattr(d, "get") else "")

                if not diff_content and not new_path:
                    logger.debug(f"diff 条目缺少 diff 和 new_path，跳过: {d}")
                    continue

                result.append({
                    "diff": diff_content or "",
                    "new_path": new_path or "unknown",
                    "old_path": old_path or "unknown",
                    "new_file": d.get("new_file", False) if isinstance(d, dict) else getattr(d, "new_file", False),
                    "deleted_file": d.get("deleted_file", False) if isinstance(d, dict) else getattr(d, "deleted_file", False),
                    "renamed_file": d.get("renamed_file", False) if isinstance(d, dict) else getattr(d, "renamed_file", False),
                })

            logger.debug(
                f"commit {commit_sha[:8]} diff: 原始 {len(list(diffs_raw)) if hasattr(diffs_raw, '__len__') else '?'} 条, "
                f"有效 {len(result)} 条, "
                f"总字节 {sum(len(r.get('diff', '')) for r in result)}"
            )
            return result

        except Exception as e:
            logger.error(f"获取提交差异失败 (project={project_id}, sha={commit_sha[:8]}): {type(e).__name__}: {e}")
            return []

    def get_file_content(
        self,
        project_id: int,
        file_path: str,
        ref: str = "main"
    ) -> Optional[str]:
        """
        获取文件内容

        Args:
            project_id: 项目 ID
            file_path: 文件路径
            ref: 分支或提交引用

        Returns:
            Optional[str]: 文件内容，失败返回 None
        """
        try:
            project = self.client.projects.get(project_id)
            file = project.files.get(file_path=file_path, ref=ref)
            return file.decode()
        except Exception as e:
            logger.error(f"获取文件内容失败: {file_path}, {e}")
            return None

    def get_commit_info(
        self,
        project_id: int,
        commit_sha: str
    ) -> Optional[Dict[str, Any]]:
        """
        获取单个提交的详细信息

        Args:
            project_id: 项目 ID
            commit_sha: 提交 SHA

        Returns:
            Optional[Dict]: 提交信息
        """
        try:
            project = self.client.projects.get(project_id)
            commit = project.commits.get(commit_sha)
            return {
                "id": commit.id,
                "short_id": commit.short_id,
                "title": commit.title,
                "message": commit.message,
                "author_name": commit.author_name,
                "author_email": commit.author_email,
                "created_at": commit.created_at,
                "web_url": commit.web_url,
                "parent_ids": getattr(commit, 'parent_ids', []),
                "stats": getattr(commit, 'stats', {})
            }
        except Exception as e:
            logger.error(f"获取提交信息失败: {e}")
            return None

    def compare_branches(
        self,
        project_id: int,
        from_ref: str,
        to_ref: str
    ) -> Optional[Dict[str, Any]]:
        """
        比较两个分支

        Args:
            project_id: 项目 ID
            from_ref: 起始分支
            to_ref: 目标分支

        Returns:
            Optional[Dict]: 比较结果
        """
        try:
            project = self.client.projects.get(project_id)
            compare = project.repository_compare(from_ref, to_ref)
            return {
                "commits": [
                    {
                        "id": c.id,
                        "title": c.title,
                        "author_name": c.author_name
                    }
                    for c in compare.get("commits", [])
                ],
                "diffs": compare.get("diffs", []),
                "compare_timeout": compare.get("compare_timeout", False),
                "compare_same_ref": compare.get("compare_same_ref", False)
            }
        except Exception as e:
            logger.error(f"比较分支失败: {e}")
            return None
