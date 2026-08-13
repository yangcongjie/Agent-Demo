"""
会话管理器
- SessionManager: 管理多个独立会话，每个会话有独立的 ContextManager
- 共享无状态组件（LLMClient / ToolRegistry / Parser）
- 支持创建 / 获取 / 列出 / 关闭会话
"""

import logging
from typing import Optional

from demo.agent_runtime import AgentRuntime
from demo.context_manager import ContextManager
from demo.llm.llm_client import LLMClient
from demo.llm.llm_output_parser import LLMOutputParser
from demo.tools.tool_registry import ToolRegistry, create_default_registry

logger = logging.getLogger(__name__)


class SessionManager:
    """
    多会话管理器

    - 所有会话共享同一个 LLMClient / ToolRegistry / Parser（无状态，可安全共享）
    - 每个会话有独立的 ContextManager（对话历史隔离）
    - 每个会话对应一个 AgentRuntime 实例
    """

    def __init__(self):
        # 共享的无状态组件（只创建一次）
        self._llm_client = LLMClient()
        self._tool_registry: Optional[ToolRegistry] = None  # 延迟初始化，等 MCP 代理启动后
        self._parser = LLMOutputParser()

        # session_id → AgentRuntime
        self._sessions: dict[str, AgentRuntime] = {}

    def init_tool_registry(self) -> None:
        """延迟初始化工具注册表（需在 MCP 代理启动后调用）"""
        self._tool_registry = create_default_registry()
        logger.info(f"工具注册表初始化完成，共 {len(self._tool_registry)} 个工具")

    def get_or_create(self, session_id: str) -> AgentRuntime:
        """获取或创建会话"""
        if session_id in self._sessions:
            logger.info(f"复用已有会话: {session_id}")
            return self._sessions[session_id]

        # 每个会话独立的 ContextManager
        context_manager = ContextManager(
            system_prompt="你是杨聪杰的智能助手，可以通过调用工具来帮助用户完成任务。"
            "请根据用户的问题，判断是否需要使用工具。如果需要，调用合适的工具；"
            "如果不需要或已获得足够信息，直接给出最终回答。"
        )

        runtime = AgentRuntime(
            llm_client=self._llm_client,
            tool_registry=self._tool_registry,
            parser=self._parser,
            context_manager=context_manager,
        )

        self._sessions[session_id] = runtime
        logger.info(f"创建新会话: {session_id} (当前共 {len(self._sessions)} 个活跃会话)")
        return runtime

    def get_session(self, session_id: str) -> Optional[AgentRuntime]:
        """获取已有会话（不存在返回 None）"""
        return self._sessions.get(session_id)

    def list_sessions(self) -> list[str]:
        """列出所有会话 ID"""
        return list(self._sessions.keys())

    def close_session(self, session_id: str) -> bool:
        """关闭会话，释放资源"""
        if session_id in self._sessions:
            del self._sessions[session_id]
            logger.info(f"关闭会话: {session_id} (剩余 {len(self._sessions)} 个)")
            return True
        return False

    @property
    def session_count(self) -> int:
        return len(self._sessions)
