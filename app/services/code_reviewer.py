# app/services/code_reviewer.py
"""LLM 代码审查服务"""
import asyncio
from typing import List, Dict, Optional, Any
import httpx
from loguru import logger


class CodeReviewer:
    """LLM 代码审查服务"""

    DEFAULT_PROMPT = """请作为资深代码审查专家，审查以下代码变更：

## 代码差异
{diff}

## 审查要求
1. 代码质量：检查代码是否清晰、可读、可维护
2. 潜在问题：识别可能的 bug、安全漏洞、性能问题
3. 最佳实践：建议改进代码以符合最佳实践
4. 命名规范：检查变量、函数、类的命名是否合理
5. 代码重复：识别重复代码并建议重构

请用中文回复，格式如下：
### 代码质量评估
[评估内容]

### 发现的问题
- [问题描述1]
- [问题描述2]

### 改进建议
- [建议1]
- [建议2]

### 总体评价
[总结]
"""

    def __init__(
        self,
        api_url: str,
        api_key: str,
        model: str,
        timeout: int = 120,
        max_retries: int = 3,
        retry_delay: int = 5
    ):
        """
        初始化代码审查服务

        Args:
            api_url: LLM API 地址
            api_key: API 密钥
            model: 模型名称
            timeout: 超时时间（秒）
            max_retries: 最大重试次数
            retry_delay: 重试延迟（秒）
        """
        self.api_url = api_url
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay

        # 创建异步 HTTP 客户端
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
        )

    async def review(
        self,
        diff: str,
        prompt_template: str = None,
        system_prompt: str = None
    ) -> Optional[str]:
        """
        审查代码差异

        Args:
            diff: 代码差异
            prompt_template: 提示词模板，必须包含 {diff} 占位符
            system_prompt: 系统提示

        Returns:
            Optional[str]: 审查结果，失败返回 None
        """
        if not diff or not diff.strip():
            logger.warning("代码差异为空，跳过审查")
            return None

        # 调试：记录最终发送给 LLM 的内容前200字符
        preview = diff[:200].replace('\n', '\\n')
        logger.debug(f"发送给 LLM 的 diff 内容预览: {preview}...")

        # 使用默认提示词或自定义模板
        prompt = prompt_template
        if not prompt or '{diff}' not in prompt:
            # 自定义模板为空或不包含 {diff} 占位符，使用默认模板
            if prompt:
                logger.warning(f"自定义模板不包含 {{diff}} 占位符，使用默认模板")
            prompt = self.DEFAULT_PROMPT
        
        # 使用 replace 而不是 format，避免 diff 中的 { } 被误解析为占位符
        user_message = prompt.replace('{diff}', diff)

        # 记录使用的提示词类型
        if prompt_template and '{diff}' in prompt_template:
            logger.info(f"使用用户自定义提示词模板 (长度: {len(prompt_template)})")
        else:
            logger.info(f"使用默认提示词模板 (长度: {len(prompt)})")
        
        # 调试：记录发送给 LLM 的完整提示词前500字符
        prompt_preview = user_message[:500].replace('\n', '\\n')
        logger.debug(f"发送给 LLM 的完整提示词预览: {prompt_preview}...")

        # 构建消息
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        else:
            messages.append({
                "role": "system",
                "content": "你是一位资深代码审查专家，擅长识别代码问题并提供改进建议。"
            })
        messages.append({"role": "user", "content": user_message})

        # 带重试的 API 调用
        for attempt in range(self.max_retries):
            try:
                response = await self.client.post(
                    self.api_url,
                    json={
                        "model": self.model,
                        "messages": messages,
                        "temperature": 0.7,
                        "max_tokens": 4096
                    }
                )
                response.raise_for_status()

                data = response.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content")

                if content:
                    logger.info(f"代码审查完成")
                    return content
                else:
                    logger.warning("API 返回空内容")
                    return None

            except httpx.TimeoutException as e:
                logger.warning(f"API 请求超时 (尝试 {attempt + 1}/{self.max_retries}): {e}")
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(self.retry_delay)
                else:
                    logger.error("达到最大重试次数，放弃请求")
                    return None

            except httpx.HTTPStatusError as e:
                logger.error(f"API 请求失败: {e.response.status_code} - {e}")
                return None

            except Exception as e:
                logger.error(f"API 请求异常: {e}")
                return None

        return None

    async def review_commit(
        self,
        commit_info: Dict[str, Any],
        diffs: List[Dict[str, Any]],
        prompt_template: str = None
    ) -> Optional[str]:
        """
        审查单个提交

        Args:
            commit_info: 提交信息
            diffs: 差异列表
            prompt_template: 提示词模板

        Returns:
            Optional[str]: 审查结果
        """
        # 格式化差异
        formatted_diff = self._format_diff(diffs)
        
        # 调试：记录传递给 LLM 的内容长度
        logger.info(
            f"review_commit: 提交={commit_info.get('short_id', 'N/A')}, "
            f"diffs数量={len(diffs)}, formatted_diff长度={len(formatted_diff)}"
        )
        
        # 如果格式化后内容为空，提前返回
        if not formatted_diff.strip():
            logger.warning(f"提交 {commit_info.get('short_id', 'N/A')} 的格式化差异为空，跳过审查")
            return None

        # 构建包含提交信息的完整差异
        full_diff = f"""## 提交信息
- **ID**: {commit_info.get('id', 'N/A')}
- **标题**: {commit_info.get('title', 'N/A')}
- **作者**: {commit_info.get('author_name', 'N/A')}
- **时间**: {commit_info.get('created_at', 'N/A')}
- **消息**: {commit_info.get('message', 'N/A')}

## 代码变更
{formatted_diff}
"""

        # 构建系统提示
        system_prompt = f"""你正在审查由 {commit_info.get('author_name', '未知作者')} 提交的代码变更。
请仔细检查代码质量、潜在问题，并提供建设性的改进建议。"""

        return await self.review(
            diff=full_diff,
            prompt_template=prompt_template,
            system_prompt=system_prompt
        )

    async def review_by_author(
        self,
        author: str,
        commits: List[Dict[str, Any]],
        diffs: List[Dict[str, Any]],
        prompt_template: str = None
    ) -> Optional[str]:
        """
        按作者审查多个提交的汇总

        Args:
            author: 作者名称
            commits: 提交列表
            diffs: 差异列表
            prompt_template: 提示词模板

        Returns:
            Optional[str]: 审查结果
        """
        if not diffs:
            logger.warning(f"作者 {author} 无代码差异，跳过审查")
            return None

        formatted_diff = self._format_diff(diffs)

        commit_count = len(commits)
        commit_titles = "\n".join(f"- {c.get('title', 'N/A')}" for c in commits[:10])

        full_diff = f"""## 作者: {author}
- **提交数量**: {commit_count}
- **主要变更**:
{commit_titles}

## 代码变更
{formatted_diff}
"""

        system_prompt = (
            f"你正在审查由 {author} 提交的所有代码变更汇总。"
            "请从整体角度评估代码质量，识别重复模式和系统性问题。"
        )

        return await self.review(
            diff=full_diff,
            prompt_template=prompt_template,
            system_prompt=system_prompt
        )

    def _format_diff(self, diffs: List[Dict[str, Any]]) -> str:
        """
        格式化差异列表

        Args:
            diffs: 差异列表

        Returns:
            str: 格式化后的差异字符串
        """
        formatted_parts = []
        total_diff_len = 0

        for diff in diffs:
            new_path = diff.get("new_path", "unknown")
            old_path = diff.get("old_path", "unknown")
            is_new = diff.get("new_file", False)
            is_deleted = diff.get("deleted_file", False)
            diff_content = diff.get("diff", "")

            if is_new:
                header = f"### [NEW FILE] {new_path}"
            elif is_deleted:
                header = f"### [DELETED] {old_path}"
            else:
                header = f"### {new_path}"

            # 只添加有内容的差异
            if diff_content.strip():
                formatted_parts.append(f"{header}\n```\n{diff_content}\n```")
                total_diff_len += len(diff_content)
            else:
                # 空差异也记录，但标记
                formatted_parts.append(f"{header}\n```\n(空变更)\n```")

        result = "\n\n".join(formatted_parts)
        logger.debug(f"_format_diff: 输入 {len(diffs)} 个差异, 输出 {len(result)} 字符, diff内容 {total_diff_len} 字符")
        return result

    def get_default_prompt(self) -> str:
        """获取默认提示词"""
        return self.DEFAULT_PROMPT

    async def close(self):
        """关闭客户端"""
        await self.client.aclose()

    async def __aenter__(self):
        """异步上下文管理器入口"""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器退出"""
        await self.close()
