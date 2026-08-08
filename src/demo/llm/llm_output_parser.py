"""
LLM 输出解析器
- ParsedOutput: 统一的解析结果（动作 + 思考 + 工具调用/最终答案）
- LLMOutputParser: 解析 LLMResponse，支持两种模式
  模式A: 原生 Function Calling（优先，直接读 tool_calls）
  模式B: Prompt 降级（从文本中提取 <tool_call>{...}</tool_call>）
"""

import json
import re
from typing import Optional

from demo.llm.llm_client import LLMResponse
from pydantic import BaseModel


# ========== 解析结果 ==========


class ParsedOutput(BaseModel):
    """LLM 输出的解析结果，供 AgentRuntime 决策下一步"""

    action: str  # "tool_call" | "final_answer"
    thought: str = ""  # LLM 的思考/推理文本
    tool_name: str = ""  # 工具名（action=tool_call）
    tool_args: Optional[dict] = {}  # 工具参数（action=tool_call）
    tool_call_id: str = ""  # 工具调用ID（用于上下文关联）
    final_answer: str = ""  # 最终答案（action=final_answer）

    @property
    def is_tool_call(self) -> bool:
        return self.action == "tool_call"

    @property
    def is_final_answer(self) -> bool:
        return self.action == "final_answer"

    def __repr__(self) -> str:
        if self.is_tool_call:
            return f"ParsedOutput(action=tool_call, tool={self.tool_name}, args={self.tool_args})"
        return f"ParsedOutput(action=final_answer, answer={self.final_answer!r})"


# ========== 解析器 ==========
class LLMOutputParser:
    """LLM 输出解析器：双模式解析"""

    # Prompt 模式的工具调用标记（兼容 XML 标签 + 代码块两种写法）
    _TOOL_CALL_PATTERN = re.compile(
        r"<tool_call>\s*(\{.*?\})\s*</tool_call>"  # <tool_call>{...}</tool_call>
        r"|```tool_call\s*(\{.*?\})\s*```",  # ```tool_call {...}```
        re.DOTALL,
    )

    def parse(self, response: LLMResponse) -> list[ParsedOutput]:
        """
        解析 LLM 响应，返回 ParsedOutput 列表
        - 原生 function calling 可能返回多个 tool_call（一一解析）
        - Prompt 模式 / 最终答案 返回单元素列表
        """
        # ---- 模式A: 原生 Function Calling（优先）----
        if response.has_tool_calls:
            return self._parse_native_tool_calls(response)

        return self._parse_content(response.content)

    def _parse_native_tool_calls(self, response: LLMResponse) -> list[ParsedOutput]:
        """解析原生 tool_calls（模式A）"""
        results = []
        # content 作为 thought 保留（模型常在调用工具前输出推理过程）
        thought = response.content or ""
        for tc in response.tool_calls:
            results.append(
                ParsedOutput(
                    action="tool_call",
                    thought=thought,
                    tool_name=tc.get("name", ""),
                    tool_args=tc.get("arguments", {}),
                    tool_call_id=tc.get("id", ""),
                )
            )
            # 多次调用时，thought 只挂在第一个上，避免重复
            thought = ""
        return results

    def _parse_content(self, content: str) -> list[ParsedOutput]:
        """解析纯文本内容（模式B / 最终答案）"""
        if not content:
            return [ParsedOutput(action="final_answer", final_answer="")]

        # 尝试从文本中提取工具调用标记
        tool_call_json = self._extract_tool_call_json(content)
        if tool_call_json is not None:
            thought = self._strip_tool_call_markup(content).strip()
            return [
                ParsedOutput(
                    action="tool_call",
                    thought=thought,
                    tool_name=tool_call_json.get("name", ""),
                    tool_args=tool_call_json.get("arguments", {}),
                    tool_call_id="",
                )
            ]

        # 没有工具调用标记 → 最终答案
        return [ParsedOutput(action="final_answer", final_answer=content.strip())]

    def _extract_tool_call_json(self, content: str) -> Optional[dict]:
        """从文本中提取第一个工具调用的 JSON 对象"""
        match = self._TOOL_CALL_PATTERN.search(content)
        if not match:
            return None
        raw_json = match.group(1) or match.group(2)
        try:
            data = json.loads(raw_json)
        except json.JSONDecodeError:
            return None
        # 容错：arguments 可能被模型写成字符串
        if isinstance(data.get("arguments"), str):
            try:
                data["arguments"] = json.loads(data["arguments"])
            except json.JSONDecodeError:
                data["arguments"] = {"_raw": data["arguments"]}
        return data

    def _strip_tool_call_markup(self, content: str) -> str:
        """移除工具调用标记，返回剩余的思考文本"""
        return self._TOOL_CALL_PATTERN.sub("", content)


# ========== 自测入口 ==========
if __name__ == "__main__":
    parser = LLMOutputParser()

    print("===== 测试1: 原生 Function Calling =====")
    resp1 = LLMResponse(
        content="我需要计算一下这个表达式",
        tool_calls=[
            {
                "id": "call_001",
                "name": "calculator",
                "arguments": {"expression": "(1+2)*3"},
            }
        ],
    )
    for po in parser.parse(resp1):
        print(po)
        print(f"  thought={po.thought!r}")

    print("\n===== 测试2: Prompt 模式（XML 标记）=====")
    resp2 = LLMResponse(
        content='让我查一下天气\n<tool_call>{"name":"weather","arguments":{"city":"北京"}}</tool_call>',
    )
    for po in parser.parse(resp2):
        print(po)
        print(f"  thought={po.thought!r}")

    print("\n===== 测试3: Prompt 模式（代码块标记）=====")
    resp3 = LLMResponse(
        content='计算如下:\n```tool_call\n{"name":"calculator","arguments":{"expression":"2**10"}}\n```',
    )
    for po in parser.parse(resp3):
        print(po)
        print(f"  thought={po.thought!r}")

    print("\n===== 测试4: 最终答案 =====")
    resp4 = LLMResponse(content="(1+2)*3 的结果是 9。")
    for po in parser.parse(resp4):
        print(po)
