"""
Agent 运行时 —— 核心循环
- AgentRuntime: 编排 LLMClient + ToolRegistry + LLMOutputParser + ContextManager
- 核心循环: 调用LLM → 解析输出 → 执行工具/返回答案 → 工具结果回填context → 重复
- 支持: 最大轮次限制、工具异常容错(错误喂回LLM)、trace日志
"""

import logging
from datetime import datetime
from typing import Optional
from demo.config import config
from demo.context_manager import ContextManager
from demo.llm.llm_client import LLMClient, LLMResponse
from demo.llm.llm_output_parser import LLMOutputParser, ParsedOutput
from demo.tools.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)  # "demo.agent_runtime"，继承 "demo" logger 配置

# 默认系统提示词
_DEFAULT_SYSTEM_PROMPT = """你是杨聪杰的智能助手，可以通过调用工具来帮助用户完成任务。
请根据用户的问题，判断是否需要使用工具。如果需要，调用合适的工具；
如果不需要或已获得足够信息，直接给出最终回答。"""


class AgentRuntime:
    """Agent 主程序"""

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        tool_registry: Optional[ToolRegistry] = None,
        parser: Optional[LLMOutputParser] = None,
        context_manager: Optional[ContextManager] = None,
        system_prompt: str = _DEFAULT_SYSTEM_PROMPT,
    ):
        self.llm_client = llm_client or LLMClient()
        self.tool_registry = tool_registry or self._build_default_registry()
        self.parser = parser or LLMOutputParser()
        self.context_manager = context_manager or ContextManager(system_prompt)
        self._trace: list[dict] = []  # 本次 run 的工具调用 trace

    def run(self, user_input: str) -> str:
        """
        Agent主流程
        """
        logger.info(f"1.开始处理用户输入: {user_input}")
        # 1. 用户消息加入上下文
        self.context_manager.add_user_message(user_input)
        logger.info(f"2.新增上下文: {self.context_manager.get_messages()}")
        self._trace = []

        logger.info(f"3.开始核心循环")
        # 2. 核心循环
        for loop_idx in range(config.AGENT_MAX_LOOP):
            logger.info(f"3-1.第 {loop_idx + 1} 轮循环")

            # 2.1 调用 LLM
            logger.info(
                f"3-2.调用LLM\n上下文: {self.context_manager.get_messages()}\n工具: {self.tool_registry.get_all_schemas()}"
            )
            try:
                response = self.llm_client.chat(
                    messages=self.context_manager.get_messages(),
                    tools=self.tool_registry.get_all_schemas(),
                )
            except Exception as e:
                logger.error(f"LLM 调用失败: {e}")
                return f"抱歉，调用大模型时出错: {e}"

            # 2.2 解析 LLM 输出
            parsed_list = self.parser.parse(response)
            logger.info(f"4.解析结果: {parsed_list}")

            # 2.3 遍历解析结果，逐个处理工具调用或最终答案
            logger.info(f"5.遍历解析结果，逐个处理工具调用或最终答案")
            has_tool_call = False
            for po in parsed_list:
                if po.is_tool_call:
                    logger.info(f"5-1.处理工具调用: {po}")
                    has_tool_call = True
                    self._handle_tool_call(po, response)
                elif po.is_final_answer:
                    # 最终答案加入上下文并返回
                    logger.info(f"5-2.处理最终答案: {po}")
                    self.context_manager.add_assistant_message(
                        content=po.final_answer,
                    )
                    return po.final_answer

            # 2.4 如果本轮全是工具调用，把 assistant 消息（含 tool_calls）加入上下文
            #     然后继续下一轮让 LLM 看到工具结果
            logger.info(f"6.判断本轮是否全为工具调用")
            if has_tool_call:
                # assistant 消息需要带上 tool_calls，让 API 知道工具结果的对应关系
                logger.info(f"6-1.把 assistant 消息（含 tool_calls）加入上下文")
                self.context_manager.add_assistant_message(
                    content=response.content or "",
                    tool_calls=response.tool_calls,
                )
                continue

            # 2.5 既没有工具调用也没有最终答案（空输出），让 LLM 重试
            logger.warning("7.LLM 输出为空，重试中...")
            self.context_manager.add_assistant_message(content="")

        # 3. 超过最大循环次数，返回兜底回答
        fallback = f"8.已达到最大工具调用轮次({config.AGENT_MAX_LOOP})，无法继续处理。"
        logger.warning(fallback)
        return fallback

    def _handle_tool_call(self, po: ParsedOutput, response: LLMResponse) -> None:
        """
        处理单个工具调用
        - 执行工具
        - 异常时把错误信息作为工具结果喂回 LLM（不 crash）
        - 记录 trace
        """
        logger.info(f"处理工具调用: {po}")

        tool_name = po.tool_name
        tool_args = po.tool_args
        tool_call_id = po.tool_call_id

        # 记录 trace
        trace_entry = {
            "timestamp": datetime.now().isoformat(),
            "tool": tool_name,
            "args": tool_args,
            "result": None,
            "error": None,
        }
        logger.info(f"记录 trace: {trace_entry}")

        # 判断是否已经调用过相同工具+相同参数，避免重复调用
        already_called = any(
            t.get("tool") == tool_name and t.get("args") == tool_args
            for t in self._trace
        )
        if already_called:
            logger.warning(f"工具 '{tool_name}' 已用相同参数 {tool_args} 调用过，跳过重复调用")
            # 把之前的执行结果喂回 LLM，避免上下文断裂
            prev = next(
                t for t in self._trace
                if t.get("tool") == tool_name and t.get("args") == tool_args
            )
            self.context_manager.add_tool_result(
                tool_call_id=tool_call_id,
                result=prev.get("result") or prev.get("error") or "[无结果]",
            )
            return

        # 执行工具
        try:

            result = self.tool_registry.execute(tool_name, **tool_args)
            trace_entry["result"] = str(result)
            logger.info(f"[工具调用] {tool_name}({tool_args}) → {result}")

            # 工具结果加入上下文（喂回 LLM）
            self.context_manager.add_tool_result(
                tool_call_id=tool_call_id,
                result=str(result),
            )

        except Exception as e:
            # 工具执行失败：错误信息也喂回 LLM，让它自行处理
            error_msg = f"工具 '{tool_name}' 执行失败: {e}"
            trace_entry["error"] = error_msg
            logger.error(f"[工具异常] {tool_name}: {e}")

            self.context_manager.add_tool_result(
                tool_call_id=tool_call_id,
                result=error_msg,
            )

        self._trace.append(trace_entry)

    @property
    def trace(self) -> list[dict]:
        """获取本次 run 的工具调用 trace"""
        return list(self._trace)

    @staticmethod
    def _build_default_registry() -> ToolRegistry:
        """构建默认工具注册表"""
        from demo.tools.tool_registry import create_default_registry

        return create_default_registry()
