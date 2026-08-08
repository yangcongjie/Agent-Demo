"""
Tool: 单个工具的抽象
- name: 工具名称
- description: 工具描述（告诉LLM何时使用）
- parameters: JSON Schema 格式的参数定义
- func: 工具实际执行函数
"""

from typing import Any, Callable
from pydantic import BaseModel


class Tool(BaseModel):
    """单个工具的抽象"""

    name: str = ""  # 工具名称
    description: str = ""  # 工具描述
    parameters: dict = {}  # JSON Schema 格式参数定义
    func: Callable[..., Any] = None  # 工具实现函数

    def execute(self, **kwargs) -> Any:
        """执行工具，透传关键字参数"""
        return self.func(**kwargs)

    def to_schema(self) -> dict:
        """导出为 OpenAI Function Calling 兼容的 schema"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
