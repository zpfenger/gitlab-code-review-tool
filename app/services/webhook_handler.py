"""GitLab Webhook 处理器 — 解析 MR/Push 事件，获取 changes/commits，回写 notes"""
import re
import time
import fnmatch
from typing import Optional
from urllib.parse import urljoin

import requests
from loguru import logger


def filter_changes(changes: list, supported_extensions: str) -> list:
    """过滤变更文件，只保留支持的文件类型"""
    ext_list = [e.strip() for e in supported_extensions.split(",") if e.strip()]

    filtered = []
    for item in changes:
        if item.get("deleted_file"):
            continue
        new_path = item.get("new_path", "")
        if not any(new_path.endswith(ext) for ext in ext_list):
            continue
        filtered.append({
            "diff": item.get("diff", ""),
            "new_path": new_path,
            "additions": len(re.findall(r"^\+(?!\+\+)", item.get("diff", ""), re.MULTILINE)),
            "deletions": len(re.findall(r"^-(?!--)", item.get("diff", ""), re.MULTILINE)),
        })
    return filtered


def match_branch(branch: str, patterns: list) -> bool:
    """检查分支是否匹配给定的模式列表（支持通配符）"""
    if not patterns:
        return True
    patterns = [p.strip() for p in patterns if p.strip()]
    if not patterns:
        return True
    return any(fnmatch.fnmatch(branch, p) for p in patterns)


def slugify_url(original_url: str) -> str:
    """将 URL 转换为 slug 格式"""
    url = re.sub(r"^https?://", "", original_url)
    target = re.sub(r"[^a-zA-Z0-9]", "_", url)
    return target.rstrip("_")


class MergeRequestHandler:
    """GitLab Merge Request Webhook 处理器"""

    def __init__(self, webhook_data: dict, gitlab_token: str, gitlab_url: str):
        self.webhook_data = webhook_data
        self.gitlab_token = gitlab_token
        self.gitlab_url = gitlab_url
        self.event_type: Optional[str] = None
        self.project_id: Optional[int] = None
        self.merge_request_iid: Optional[int] = None
        self.action: Optional[str] = None
        self._parse()

    def _parse(self):
        self.event_type = self.webhook_data.get("object_kind")
        if self.event_type == "merge_request":
            mr = self.webhook_data.get("object_attributes", {})
            self.merge_request_iid = mr.get("iid")
            self.project_id = mr.get("target_project_id")
            self.action = mr.get("action")

    def get_merge_request_changes(self) -> list:
        if self.event_type != "merge_request":
            return []

        max_retries, retry_delay = 3, 10
        for attempt in range(max_retries):
            url = urljoin(
                f"{self.gitlab_url}/",
                f"api/v4/projects/{self.project_id}/merge_requests/{self.merge_request_iid}/changes?access_raw_diffs=true",
            )
            resp = requests.get(
                url, headers={"Private-Token": self.gitlab_token}, verify=False
            )
            if resp.status_code == 200:
                changes = resp.json().get("changes", [])
                if changes:
                    return changes
                logger.info(f"Changes 为空，{retry_delay}s 后重试 ({attempt + 1}/{max_retries})")
                time.sleep(retry_delay)
            else:
                logger.warning(f"获取 MR changes 失败: {resp.status_code}")
                return []

        logger.warning("获取 MR changes 达到最大重试次数")
        return []

    def get_merge_request_commits(self) -> list:
        if self.event_type != "merge_request":
            return []

        url = urljoin(
            f"{self.gitlab_url}/",
            f"api/v4/projects/{self.project_id}/merge_requests/{self.merge_request_iid}/commits",
        )
        resp = requests.get(
            url, headers={"Private-Token": self.gitlab_token}, verify=False
        )
        if resp.status_code == 200:
            return resp.json()
        logger.warning(f"获取 MR commits 失败: {resp.status_code}")
        return []

    def add_merge_request_notes(self, review_result: str):
        url = urljoin(
            f"{self.gitlab_url}/",
            f"api/v4/projects/{self.project_id}/merge_requests/{self.merge_request_iid}/notes",
        )
        resp = requests.post(
            url,
            headers={
                "Private-Token": self.gitlab_token,
                "Content-Type": "application/json",
            },
            json={"body": review_result},
            verify=False,
        )
        if resp.status_code == 201:
            logger.info("MR note 添加成功")
        else:
            logger.error(f"MR note 添加失败: {resp.status_code} {resp.text}")

    def target_branch_protected(self) -> bool:
        url = urljoin(
            f"{self.gitlab_url}/",
            f"api/v4/projects/{self.project_id}/protected_branches",
        )
        resp = requests.get(
            url, headers={"Private-Token": self.gitlab_token}, verify=False
        )
        if resp.status_code == 200:
            target_branch = self.webhook_data["object_attributes"]["target_branch"]
            return any(
                fnmatch.fnmatch(target_branch, item["name"]) for item in resp.json()
            )
        return False


class PushHandler:
    """GitLab Push Webhook 处理器"""

    def __init__(self, webhook_data: dict, gitlab_token: str, gitlab_url: str):
        self.webhook_data = webhook_data
        self.gitlab_token = gitlab_token
        self.gitlab_url = gitlab_url
        self.event_type: Optional[str] = None
        self.project_id: Optional[int] = None
        self.branch_name: Optional[str] = None
        self.commit_list: list = []
        self._parse()

    def _parse(self):
        self.event_type = self.webhook_data.get("event_name")
        if self.event_type == "push":
            self.project_id = self.webhook_data.get("project_id") or self.webhook_data.get("project", {}).get("id")
            self.branch_name = self.webhook_data.get("ref", "").replace("refs/heads/", "")
            self.commit_list = self.webhook_data.get("commits", [])

    def get_push_commits(self) -> list:
        if self.event_type != "push":
            return []
        return [
            {
                "message": c.get("message"),
                "author": c.get("author", {}).get("name"),
                "timestamp": c.get("timestamp"),
                "url": c.get("url"),
            }
            for c in self.commit_list
        ]

    def get_push_changes(self) -> list:
        if self.event_type != "push" or not self.commit_list:
            return []

        before = self.webhook_data.get("before", "")
        after = self.webhook_data.get("after", "")
        if not before or not after:
            return []

        if after.startswith("0000000"):
            logger.info("检测到分支删除，无需审查")
            return []

        if before.startswith("0000000"):
            logger.info("检测到新分支创建，使用 commit diff API")
            return self._get_commit_diff(after)

        logger.info(f"比较提交 {before} → {after}")
        return self._repository_compare(before, after)

    def _repository_compare(self, before: str, after: str) -> list:
        url = f"{urljoin(f'{self.gitlab_url}/', f'api/v4/projects/{self.project_id}/repository/compare')}?from={before}&to={after}"
        resp = requests.get(
            url, headers={"Private-Token": self.gitlab_token}, verify=False
        )
        if resp.status_code == 200:
            return resp.json().get("diffs", [])
        logger.warning(f"repository compare 失败: {resp.status_code}")
        return []

    def _get_commit_diff(self, commit_sha: str) -> list:
        url = urljoin(
            f"{self.gitlab_url}/",
            f"api/v4/projects/{self.project_id}/repository/commits/{commit_sha}/diff",
        )
        resp = requests.get(
            url, headers={"Private-Token": self.gitlab_token}, verify=False
        )
        if resp.status_code == 200:
            return resp.json()
        logger.warning(f"获取 commit diff 失败: {resp.status_code}")
        return []

    def add_push_notes(self, message: str):
        if not self.commit_list:
            return
        last_commit_id = self.commit_list[-1].get("id")
        if not last_commit_id:
            return
        url = urljoin(
            f"{self.gitlab_url}/",
            f"api/v4/projects/{self.project_id}/repository/commits/{last_commit_id}/comments",
        )
        resp = requests.post(
            url,
            headers={
                "Private-Token": self.gitlab_token,
                "Content-Type": "application/json",
            },
            json={"note": message},
            verify=False,
        )
        if resp.status_code == 201:
            logger.info("Push comment 添加成功")
        else:
            logger.error(f"Push comment 添加失败: {resp.status_code}")
