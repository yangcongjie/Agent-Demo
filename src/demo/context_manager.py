"""
上下文管理器
- ContextManager: 管理对话消息历史（OpenAI 格式）
- 支持多轮对话、工具调用结果、消息压缩
- 压缩策略: 超过 CONTEXT_MAX_KEEP_MESSAGES 时，保留 system + 最近 N 条
"""

import json
import logging
from typing import Optional

from demo.config import config

logger = logging.getLogger(__name__)  # "demo.context_manager"


class ContextManager:
    """对话上下文管理器"""

    def __init__(self, system_prompt: str = ""):
        self._messages: list[dict] = []
        if system_prompt:
            self._messages.append({"role": "system", "content": system_prompt})

    def add_user_message(self, content: str) -> None:
        """添加用户消息"""
        self._messages.append({"role": "user", "content": content})

    def add_assistant_message(
        self,
        content: str = "",
        tool_calls: Optional[list[dict]] = None,
    ) -> None:
        """
        添加助手消息
        :param content: 文本内容（思考/回复）
        :param tool_calls: 来自 LLMResponse.tool_calls，格式 [{"id","name","arguments"}]
        """
        msg: dict = {"role": "assistant", "content": content or ""}
        if tool_calls:
            # 转换为 OpenAI API 要求的格式
            msg["tool_calls"] = [
                {
                    "id": tc.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": tc.get("name", ""),
                        "arguments": json.dumps(
                            tc.get("arguments", {}), ensure_ascii=False
                        ),
                    },
                }
                for tc in tool_calls
            ]
        self._messages.append(msg)

    def add_tool_result(self, tool_call_id: str, result: str) -> None:
        """
        添加工具执行结果
        :param tool_call_id: 对应的 tool_call ID
        :param result: 工具执行返回的文本
        """
        self._messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": result,
        })

    def get_messages(self) -> list[dict]:
        """获取完整消息列表（传给 LLM）"""
        self.maybe_compress()
        return list(self._messages)

    def maybe_compress(self) -> None:
        """
        压缩策略: 超过 CONTEXT_MAX_KEEP_MESSAGES 时
        保留 system 消息 + 最近 N 条消息
        """
        max_keep = config.CONTEXT_MAX_KEEP_MESSAGES
        # system 消息不计数
        system_count = 1 if self._messages and self._messages[0]["role"] == "system" else 0
        non_system = self._messages[system_count:]

        if len(non_system) <= max_keep:
            return

        # 保留最近 max_keep 条
        kept = non_system[-max_keep:]
        self._messages = self._messages[:system_count] + kept
        logger.info(f"上下文压缩: {len(non_system)} → {max_keep} 条")

    @property
    def message_count(self) -> int:
        return len(self._messages)

    def clear(self) -> None:
        """清空上下文（保留 system 消息）"""
        if self._messages and self._messages[0]["role"] == "system":
            self._messages = [self._messages[0]]
        else:
            self._messages = []
