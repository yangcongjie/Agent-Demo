"""
LLM 客户端
- LLMClient: 封装大模型 API 调用（兼容 OpenAI / DeepSeek 接口格式）
- LLMResponse: 统一的响应结构（content + tool_calls + raw）
- 支持 Function Calling（原生 tool_calls），降级时由 Parser 处理 Prompt 模式
"""

import json
import logging
from typing import Any, Dict, List, Optional

import requests
from pydantic import BaseModel

from demo.config import config

logger = logging.getLogger(__name__)  # "demo.llm.llm_client"


# ========== 统一响应结构 ==========
class LLMResponse(BaseModel):
    """LLM 调用的统一响应，屏蔽底层 API 差异"""

    id: str = ""  # 响应 ID
    model: str = ""  # 模型名称
    finish_reason: str = ""  # 结束原因（stop/tool_calls 等）

    content: str = ""  # 模型输出内容
    think: Optional[str] = None  # 模型思考过程

    tool_calls: Optional[list[dict]] = None  # 工具调用信息

    prompt_tokens: int = 0  # 输入token数
    completion_tokens: int = 0  # 输出token数
    total_tokens: int = 0  # 总token数

    raw: Optional[dict] = None  # 原始http返回完整dict，线上debug神器

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0

    def __repr__(self) -> str:
        """打印输出"""
        return f"LLMResponse(content={self.content!r}, tool_calls={self.tool_calls})"


# ========== 异常 ==========
class LLMError(RuntimeError):
    """LLM 调用异常基类"""

    def __init__(self, message: str, status_code: Optional[int] = None, body: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


# ========== LLM 客户端 ==========
class LLMClient:
    """大模型 API 客户端（兼容 OpenAI / DeepSeek 接口）"""

    def __init__(
        self,
        api_key: str = None,
        base_url: str = None,
        model: str = None,
        temperature: float = None,
        timeout: float = 120.0,
    ):
        # 未显式传入则从 Config 读取
        self.api_key = api_key or config.LLM_API_KEY
        self.base_url = (base_url or config.LLM_BASE_URL).rstrip("/")
        self.model = model or config.LLM_MODEL
        self.temperature = (
            temperature if temperature is not None else config.LLM_TEMPERATURE
        )
        self.timeout = timeout

    def chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        tool_choice: str = "auto",
    ) -> LLMResponse:
        """
        调用 LLM 进行一次对话
        :param messages: OpenAI 格式的消息列表
        :param tools: 工具 schema 列表（来自 ToolRegistry.get_all_schemas()）
        :param tool_choice: "auto" 让模型自主决策 / "none" 禁用工具 / 指定工具名
        :return: LLMResponse
        """

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        # 仅当传入工具时才启用 function calling
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice

        # 发起请求
        logger.info(f"调用 LLM: model={self.model}, messages={len(messages)}条, tools={len(tools) if tools else 0}个")
        try:
            resp = requests.post(
                url, headers=headers, json=payload, timeout=self.timeout
            )
        except requests.Timeout:
            logger.error(f"LLM 请求超时（{self.timeout}s）")
            raise LLMError(f"LLM 请求超时（{self.timeout}s）")
        except requests.RequestException as e:
            logger.error(f"LLM 网络请求失败: {e}")
            raise LLMError(f"LLM 网络请求失败: {e}")

        if resp.status_code != 200:
            logger.error(f"LLM API 返回错误 {resp.status_code}: {resp.text[:200]}")
            raise LLMError(
                f"LLM API 返回错误 {resp.status_code}",
                status_code=resp.status_code,
                body=resp.text,
            )

        # 解析响应
        try:
            data = resp.json()
        except ValueError:
            logger.error(f"LLM 响应 JSON 解析失败: {resp.text[:200]}")
            raise LLMError("LLM 响应 JSON 解析失败", body=resp.text)

        logger.info(f"LLM 响应原始数据: {data}")

        # 解析封装响应
        result = self._parse_chat_completion(data)
        logger.info(f"LLM 解析后的 响应: {result}")
        logger.info(f"LLM 响应: finish_reason={result.finish_reason}, "
                     f"tool_calls={len(result.tool_calls) if result.tool_calls else 0}个, "
                     f"tokens={result.total_tokens}")
        return result

    def _parse_chat_completion(self, data: Dict[str, Any]) -> LLMResponse:
        """
        解析响应封装为 LLMResponse 对象
        :param data: http response json dict
        :return: LLMResponse
        """
        # 校验choices
        choices = data.get("choices") or []
        if not isinstance(choices, list) or len(choices) == 0:
            raise LLMError(
                "chat.completion 响应缺少choices字段",
                body=json.dumps(data, ensure_ascii=False)
            )

        first_choice = choices[0]
        finish_reason = first_choice.get("finish_reason")
        msg = first_choice.get("message", {})

        # token统计
        usage = data.get("usage") or {}
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        total_tokens = usage.get("total_tokens", 0)

        content = msg.get("content")

        # 解析tool_calls，arguments字符串转dict，容错json解析失败
        tool_calls: List[Dict[str, Any]] = []
        raw_tool_calls = msg.get("tool_calls") or []
        for tc in raw_tool_calls:
            func = tc.get("function", {})
            args_raw = func.get("arguments", "{}")
            try:
                args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            except json.JSONDecodeError:
                args = {"_raw": args_raw}

            tool_calls.append({
                "id": tc.get("id", ""),
                "name": func.get("name", ""),
                "arguments": args
            })

        resp = LLMResponse(
            id=data.get("id", ""),
            model=data.get("model", ""),
            finish_reason=finish_reason,
            content=content,
            tool_calls=tool_calls,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            raw=data
        )
        # 千问推理内容：reasoning_content 在message内部
        resp.think = msg.get("reasoning_content")
        return resp


# ========== 自测入口 ==========
if __name__ == "__main__":
    client = LLMClient()

    try:
        resp = client.chat(
            messages=[
                {
                    "role": "system",
                    "content": "你是一个专业的私人助手，你的主人名字叫小杨，你的名字叫做小小杨",
                },
                {"role": "user", "content": "你是谁"},
            ]
        )
        print("=== 文本内容 ===")
        print(resp.content)
        print("=== 工具调用 ===")
        print(resp.tool_calls)
        if config.DEBUG_PRINT_LLM_RAW:
            print("=== 原始响应 ===")
            print(json.dumps(resp.raw, ensure_ascii=False, indent=2))
    except LLMError as e:
        print(f"[调用失败] {e.body}")
