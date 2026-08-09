"""
Tool: 单个工具的抽象
- name: 工具名称
- description: 工具描述（告诉LLM何时使用）
- parameters: JSON Schema 格式的参数定义
- func: 工具实际执行函数
"""

import logging
from typing import Any, Callable
from pydantic import BaseModel
from demo.mcp.mcp_client import call_mcp_tool

logger = logging.getLogger(__name__)

MAX_RETRY = 3  # 最大重试次数

class Tool(BaseModel):
    """单个工具的抽象"""

    name: str = ""  # 工具名称
    description: str = ""  # 工具描述
    parameters: dict = {}  # JSON Schema 格式参数定义
    executor_type: str = ""  # 执行类型local 或 mcp
    func: Callable[..., Any] = None  # 工具实现函数

    def execute(self, **kwargs) -> Any:
        """执行工具，失败自动重试，超过 MAX_RETRY 次返回错误文本"""
        last_error = None
        for attempt in range(1, MAX_RETRY + 1):
            try:
                if self.executor_type == "local":
                    return self.func(**kwargs)
                elif self.executor_type == "mcp":
                    return call_mcp_tool(self.name, **kwargs)
                else:
                    raise ValueError(f"未知执行类型: {self.executor_type}")
            except Exception as e:
                last_error = e
                logger.warning(f"工具 '{self.name}' 第 {attempt}/{MAX_RETRY} 次执行失败: {e}")
                if attempt < MAX_RETRY:
                    import time
                    time.sleep(0.5 * attempt)  # 递增等待：0.5s, 1s

        # 超过重试上限，返回错误文本喂给 LLM
        err_msg = f"[工具 '{self.name}' 连续 {MAX_RETRY} 次执行失败，已跳过: {last_error}]"
        logger.error(err_msg)
        return err_msg

    def to_schema(self) -> dict:
        """导出为 OpenAI Function Calling 兼容的 schema"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
                "executor_type": self.executor_type
            },
        }
