# app/services/svn_uploader.py
"""SVN 上传服务"""
import os
import subprocess
import shutil
from typing import List, Optional, Tuple
from pathlib import Path
from loguru import logger
import tempfile
import time


class SVNUploader:
    """SVN 上传服务"""

    def __init__(
        self,
        svn_url: str,
        username: str,
        password: str,
        max_retries: int = 3,
        retry_delay: int = 5
    ):
        """
        初始化 SVN 上传器

        Args:
            svn_url: SVN 仓库地址
            username: 用户名
            password: 密码
            max_retries: 最大重试次数
            retry_delay: 重试延迟（秒）
        """
        self.svn_url = svn_url.rstrip('/')
        self.username = username
        self.password = password
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    def test_connection(self) -> bool:
        """
        测试连接是否正常

        Returns:
            bool: 连接成功返回 True，否则返回 False
        """
        try:
            result = self._run_command("info", [self.svn_url])
            return result.returncode == 0
        except Exception as e:
            logger.error(f"SVN 连接测试失败: {e}")
            return False

    def upload_file(
        self,
        local_path: str,
        remote_path: str,
        commit_message: str = "Upload file"
    ) -> bool:
        """
        上传单个文件，支持新增和覆盖已存在的文件。

        Args:
            local_path: 本地文件路径（支持绝对路径或相对于项目根目录的路径）
            remote_path: 远程路径
            commit_message: 提交消息

        Returns:
            bool: 上传成功返回 True，否则返回 False
        """
        # 相对路径转绝对路径（相对于项目根目录，即当前工作目录）
        local_path = os.path.abspath(local_path)
        if not os.path.isfile(local_path):
            logger.warning(f"本地文件不存在: {local_path}")
            return False

        full_remote_path = f"{self.svn_url}{remote_path}"

        for attempt in range(self.max_retries):
            temp_dir = None
            try:
                # 先确保父目录存在
                parent_path = "/".join(remote_path.split("/")[:-1])
                self._ensure_directory(parent_path)

                # svn import 不支持覆盖已存在的路径，改用 checkout + update/add + commit 流程
                temp_dir = tempfile.mkdtemp(prefix="svn_upload_")
                parent_full_path = f"{self.svn_url}{parent_path}"

                # checkout 父目录到临时工作副本（--depth empty 只拉取顶层结构）
                checkout_result = self._run_command(
                    "checkout",
                    [parent_full_path, temp_dir, "--depth", "empty"]
                )
                if checkout_result.returncode != 0:
                    logger.warning(f"Checkout 父目录失败: {checkout_result.stderr}")
                    raise Exception(f"Checkout 失败: {checkout_result.stderr}")

                # 把本地文件复制到工作副本对应子目录
                filename = remote_path.split("/")[-1]
                dest_file = os.path.join(temp_dir, filename)
                os.makedirs(os.path.dirname(dest_file), exist_ok=True)
                shutil.copy2(local_path, dest_file)

                # 检查远程文件是否已存在，决定用 update 还是 add
                check_result = self._run_command("info", [full_remote_path])
                if check_result.returncode == 0:
                    logger.info(f"文件已存在，更新: {remote_path}")
                    cmd_result = self._run_command("update", [dest_file])
                    cmd_name = "update"
                else:
                    logger.info(f"文件不存在，新增: {remote_path}")
                    cmd_result = self._run_command("add", [dest_file])
                    cmd_name = "add"

                if cmd_result.returncode != 0:
                    logger.warning(f"svn {cmd_name} 失败: {cmd_result.stderr}")
                    raise Exception(f"svn {cmd_name} 失败")

                # 在 commit 前检查并解决可能的冲突状态
                self._resolve_conflict(dest_file, source_file=local_path, cwd=temp_dir)

                # 在工作副本内执行 commit（cwd=temp_dir 确保 svn 正确识别工作副本）
                result = self._run_command(
                    "commit",
                    [dest_file, "-m", commit_message],
                    cwd=temp_dir,
                )

                if result.returncode == 0:
                    logger.info(f"文件上传成功: {remote_path}")
                    return True
                else:
                    logger.warning(f"文件上传失败 (尝试 {attempt + 1}): {result.stderr}")

            except Exception as e:
                logger.warning(f"上传异常 (尝试 {attempt + 1}): {e}")

            finally:
                # 清理临时目录
                if temp_dir and os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir, ignore_errors=True)

            if attempt < self.max_retries - 1:
                time.sleep(self.retry_delay)

        logger.error(f"文件上传失败，达到最大重试次数: {remote_path}")
        return False

    def upload_directory(
        self,
        local_path: str,
        remote_path: str,
        commit_message: str = "Upload directory"
    ) -> bool:
        """
        上传整个目录

        Args:
            local_path: 本地目录路径
            remote_path: 远程路径
            commit_message: 提交消息

        Returns:
            bool: 上传成功返回 True，否则返回 False
        """
        full_remote_path = f"{self.svn_url}{remote_path}"

        for attempt in range(self.max_retries):
            try:
                # 确保远程目录存在
                self._ensure_directory(remote_path)

                # 检出远程目录到临时位置
                with tempfile.TemporaryDirectory() as temp_dir:
                    checkout_result = self._run_command(
                        "checkout",
                        [full_remote_path, temp_dir, "--depth", "empty"]
                    )

                    if checkout_result.returncode != 0:
                        logger.error(f"检出失败: {checkout_result.stderr}")
                        continue

                    # 复制本地文件到检出目录
                    local_path_obj = Path(local_path)
                    for item in local_path_obj.rglob("*"):
                        if item.is_file():
                            relative_path = item.relative_to(local_path_obj)
                            dest_path = Path(temp_dir) / relative_path

                            # 确保目标目录存在
                            dest_path.parent.mkdir(parents=True, exist_ok=True)

                            # 复制文件
                            shutil.copy2(item, dest_path)

                            # 添加到 SVN
                            self._run_command("add", [str(dest_path), "--force"])

                    # 提交更改
                    result = self._run_command(
                        "commit",
                        [temp_dir, "-m", commit_message]
                    )

                    if result.returncode == 0:
                        logger.info(f"目录上传成功: {remote_path}")
                        return True
                    else:
                        logger.warning(f"目录上传失败 (尝试 {attempt + 1}): {result.stderr}")

            except Exception as e:
                logger.warning(f"上传异常 (尝试 {attempt + 1}): {e}")

            if attempt < self.max_retries - 1:
                time.sleep(self.retry_delay)

        logger.error(f"目录上传失败，达到最大重试次数: {remote_path}")
        return False

    def upload_batch(
        self,
        reports: List[Tuple[str, str]],
        commit_message: str = "Upload batch reports"
    ) -> dict:
        """
        批量上传文件

        Args:
            reports: (本地路径, 远程路径) 元组列表
            commit_message: 提交消息

        Returns:
            dict: 文件路径到上传结果的映射
        """
        results = {}
        for local_path, remote_path in reports:
            results[remote_path] = self.upload_file(local_path, remote_path, commit_message)
        return results

    def create_directory(self, remote_path: str) -> bool:
        """
        创建远程目录

        Args:
            remote_path: 远程路径

        Returns:
            bool: 创建成功返回 True，否则返回 False
        """
        full_remote_path = f"{self.svn_url}{remote_path}"

        try:
            result = self._run_command(
                "mkdir",
                [full_remote_path, "-m", "Create directory"]
            )
            return result.returncode == 0
        except Exception as e:
            logger.error(f"创建目录失败: {e}")
            return False

    def list_files(self, remote_path: str) -> List[str]:
        """
        列出远程目录下的文件

        Args:
            remote_path: 远程路径

        Returns:
            List[str]: 文件列表
        """
        full_remote_path = f"{self.svn_url}{remote_path}"

        try:
            result = self._run_command("list", [full_remote_path])

            if result.returncode == 0:
                files = result.stdout.strip().split("\n")
                return [f for f in files if f]
            return []
        except Exception as e:
            logger.error(f"列出文件失败: {e}")
            return []

    def delete_file(
        self,
        remote_path: str,
        commit_message: str = "Delete file"
    ) -> bool:
        """
        删除远程文件

        Args:
            remote_path: 远程路径
            commit_message: 提交消息

        Returns:
            bool: 删除成功返回 True，否则返回 False
        """
        full_remote_path = f"{self.svn_url}{remote_path}"

        try:
            result = self._run_command(
                "delete",
                [full_remote_path, "-m", commit_message]
            )
            return result.returncode == 0
        except Exception as e:
            logger.error(f"删除文件失败: {e}")
            return False

    def get_file_info(self, remote_path: str) -> Optional[dict]:
        """
        获取文件信息

        Args:
            remote_path: 远程路径

        Returns:
            Optional[dict]: 文件信息
        """
        full_remote_path = f"{self.svn_url}{remote_path}"

        try:
            result = self._run_command("info", [full_remote_path])

            if result.returncode == 0:
                info = {}
                for line in result.stdout.split("\n"):
                    if ": " in line:
                        key, value = line.split(": ", 1)
                        info[key.strip()] = value.strip()
                return info
            return None
        except Exception as e:
            logger.error(f"获取文件信息失败: {e}")
            return None

    def checkout(
        self,
        remote_path: str,
        local_path: str
    ) -> bool:
        """
        检出远程目录

        Args:
            remote_path: 远程路径
            local_path: 本地路径

        Returns:
            bool: 检出成功返回 True，否则返回 False
        """
        full_remote_path = f"{self.svn_url}{remote_path}"

        try:
            result = self._run_command("checkout", [full_remote_path, local_path])
            return result.returncode == 0
        except Exception as e:
            logger.error(f"检出失败: {e}")
            return False

    def update(self, local_path: str) -> bool:
        """
        更新本地工作副本

        Args:
            local_path: 本地路径

        Returns:
            bool: 更新成功返回 True，否则返回 False
        """
        try:
            result = self._run_command("update", [local_path])
            return result.returncode == 0
        except Exception as e:
            logger.error(f"更新失败: {e}")
            return False

    def _ensure_directory(self, remote_path: str) -> bool:
        """
        确保远程目录存在

        Args:
            remote_path: 远程路径

        Returns:
            bool: 成功返回 True
        """
        if not remote_path or remote_path == "/":
            return True

        full_remote_path = f"{self.svn_url}{remote_path}"

        # 检查目录是否存在
        check_result = self._run_command("info", [full_remote_path])

        if check_result.returncode != 0:
            # 目录不存在，创建
            logger.info(f"创建远程目录: {remote_path}")
            return self.create_directory(remote_path)

        return True

    def _resolve_conflict(self, file_path: str, source_file: Optional[str] = None, cwd: Optional[str] = None) -> bool:
        """
        解决文件冲突状态

        Args:
            file_path: 工作副本中的文件路径
            source_file: 源文件路径（最新版本，用于冲突时重新复制）
            cwd: 工作目录

        Returns:
            bool: 解决成功返回 True
        """
        try:
            # 先检查文件是否处于冲突状态
            status_result = self._run_command("status", [file_path], cwd=cwd)

            if status_result.returncode != 0:
                return False

            status_output = status_result.stdout.strip()
            # SVN 冲突状态标记为 'C' 
            if 'C ' in status_output:
                logger.info(f"检测到冲突状态，尝试解决: {file_path}")

                # 先 revert 放弃本地修改（会恢复到 checkout 时的版本）
                self._run_command("revert", [file_path], cwd=cwd)

                # 删除冲突时产生的临时文件
                base_name = os.path.splitext(file_path)[0]

                for suffix in ['.mine', '.r-old', '.r-new']:
                    conflict_file = f"{base_name}{suffix}"
                    if os.path.exists(conflict_file):
                        try:
                            os.remove(conflict_file)
                            logger.debug(f"已删除冲突临时文件: {conflict_file}")
                        except OSError:
                            pass

                # 如果提供了源文件，重新复制最新版本到工作副本
                if source_file and os.path.exists(source_file):
                    shutil.copy2(source_file, file_path)
                    logger.debug(f"已重新复制最新文件: {source_file} -> {file_path}")

                # 使用 'working' 接受当前工作版本
                resolve_result = self._run_command(
                    "resolve",
                    ["--accept", "working", file_path],
                    cwd=cwd
                )

                if resolve_result.returncode == 0:
                    logger.info(f"冲突已解决: {file_path}")
                    return True
                else:
                    logger.warning(f"冲突解决失败: {resolve_result.stderr}")
                    return False

            return True

        except Exception as e:
            logger.warning(f"检查冲突状态异常: {e}")
            return False

    def _run_command(
        self,
        command: str,
        args: List[str],
        cwd: Optional[str] = None,
        stdin_data: Optional[bytes] = None,
    ) -> subprocess.CompletedProcess:
        """
        执行 SVN 命令

        Args:
            command: SVN 命令
            args: 命令参数
            cwd: 工作目录（默认为 None，使用当前目录）
            stdin_data: 标准输入数据（bytes）

        Returns:
            subprocess.CompletedProcess: 命令执行结果
        """
        cmd = self._build_command(command, args)

        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            input=stdin_data if stdin_data is not None else self.password,
            cwd=cwd,
            timeout=300,
        )

    def _build_command(self, command: str, args: List[str]) -> List[str]:
        """
        构建 SVN 命令

        Args:
            command: SVN 命令
            args: 命令参数

        Returns:
            List[str]: 完整命令
        """
        cmd = [
            "svn",
            command,
            "--username", self.username,
            "--password-from-stdin",
            "--non-interactive",
            "--trust-server-cert-failures", "unknown-ca"
        ]
        cmd.extend(args)
        return cmd
