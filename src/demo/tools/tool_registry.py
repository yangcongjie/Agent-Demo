"""
工具注册表
- ToolRegistry: 工具注册表，支持动态注册、查询、导出Schema给LLM
- 内置工具: calculator / search / weather / todo
- create_default_registry(): 一键创建包含全部内置工具的注册表
"""

import ast
import operator
import time
from typing import Any, Optional
from demo.tools.tool import Tool
import demo.mcp.mcp_client as mcp_client
import logging

logger = logging.getLogger(__name__)  # "demo.agent_runtime"，继承 "demo" logger 配置

# ========== 安全的数学表达式求值 ==========
_SAFE_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _safe_eval(expr: str) -> float:
    """使用 ast 安全解析并求值数学表达式，避免 eval 的安全风险"""
    try:
        node = ast.parse(expr, mode="eval").body
    except SyntaxError as e:
        raise ValueError(f"表达式语法错误: {e}")

    def _eval(n):
        if isinstance(n, ast.BinOp):
            op = _SAFE_OPERATORS.get(type(n.op))
            if op is None:
                raise ValueError(f"不支持的运算符: {type(n.op).__name__}")
            return op(_eval(n.left), _eval(n.right))
        if isinstance(n, ast.UnaryOp):
            op = _SAFE_OPERATORS.get(type(n.op))
            if op is None:
                raise ValueError(f"不支持的运算符: {type(n.op).__name__}")
            return op(_eval(n.operand))
        if isinstance(n, ast.Constant):  # py>=3.8
            if isinstance(n.value, (int, float)):
                return n.value
            raise ValueError(f"不支持的常量类型: {type(n.value).__name__}")
        if isinstance(n, ast.Num):  # py<3.8 兼容
            return n.n
        raise ValueError(f"不支持的表达式节点: {type(n).__name__}")

    return _eval(node)


# ========== 模拟数据 ==========
_MOCK_SEARCH_DB = {
    "python": "Python 是一种广泛使用的高级编程语言，由 Guido van Rossum 于 1991 年发布。",
    "agent": "AI Agent 是能够感知环境、自主决策并执行动作以完成目标的智能体系统。",
    "llm": "大语言模型(LLM)是基于 Transformer 架构在海量文本上训练的生成式模型。",
    "deepseek": "DeepSeek 是一家中国 AI 公司，推出 DeepSeek-Chat/DeepSeek-Coder 等开源模型。",
}

_MOCK_WEATHER_DB = {
    "北京": {"temp": 28, "weather": "晴", "humidity": 45},
    "上海": {"temp": 31, "weather": "多云", "humidity": 70},
    "广州": {"temp": 33, "weather": "雷阵雨", "humidity": 85},
    "深圳": {"temp": 32, "weather": "阵雨", "humidity": 80},
}

# 内存级待办存储
_TODO_STORE: dict = {}


# ========== 工具实现 ==========
def _tool_calculator(expression: str) -> str:
    """
    计算器工具：对数学表达式求值
    """
    result = _safe_eval(expression)
    # 整数结果去掉 .0 后缀
    if isinstance(result, float) and result.is_integer():
        result = int(result)
    return f"{expression} = {result}"


def _tool_search(query: str) -> str:
    """
    搜索工具（mock）：根据关键词返回模拟搜索结果
    """
    time.sleep(0.3)  # 模拟网络延迟
    query_lower = query.lower().strip()
    for key, value in _MOCK_SEARCH_DB.items():
        if key in query_lower:
            return f"[搜索结果] {value}"
    return f"[搜索结果] 未找到与 '{query}' 相关的信息。"


def _tool_weather(city: str) -> str:
    """
    天气查询工具（mock）：返回指定城市的模拟天气
    """
    time.sleep(0.2)  # 模拟网络延迟
    city = city.strip()
    if city in _MOCK_WEATHER_DB:
        d = _MOCK_WEATHER_DB[city]
        return (
            f"{city}今日天气: {d['weather']}, 气温 {d['temp']}°C, 湿度 {d['humidity']}%"
        )
    return f"暂未收录 '{city}' 的天气信息。已支持城市: {list(_MOCK_WEATHER_DB.keys())}"


def _tool_todo(action: str, content: str = "", task_id: int = -1) -> str:
    """
    待办管理工具
    action: add / list / done / delete
    """
    if action == "add":
        new_id = max(_TODO_STORE.keys(), default=0) + 1
        _TODO_STORE[new_id] = {"content": content, "done": False}
        return f"已添加待办 #{new_id}: {content}"
    if action == "list":
        if not _TODO_STORE:
            return "当前没有待办事项。"
        lines = []
        for tid, item in _TODO_STORE.items():
            status = "[x]" if item["done"] else "[ ]"
            lines.append(f"#{tid} {status} {item['content']}")
        return "\n".join(lines)
    if action == "done":
        if task_id < 0 or task_id not in _TODO_STORE:
            return f"待办 #{task_id} 不存在。"
        _TODO_STORE[task_id]["done"] = True
        return f"已标记待办 #{task_id} 为完成。"
    if action == "delete":
        if task_id < 0 or task_id not in _TODO_STORE:
            return f"待办 #{task_id} 不存在。"
        del _TODO_STORE[task_id]
        return f"已删除待办 #{task_id}。"
    return f"未知操作: {action}。支持的操作: add / list / done / delete"


# ========== 工具注册表 ==========
class ToolRegistry:
    """
    工具注册表：管理所有可用工具
    """

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """注册一个工具（重复注册报错）"""
        if tool.name in self._tools:
            raise ValueError(f"工具 '{tool.name}' 已注册")
        self._tools[tool.name] = tool

    def get_tool(self, name: str) -> Optional[Tool]:
        """按名称获取工具"""
        return self._tools.get(name)

    def execute(self, name: str, **kwargs) -> Any:
        """按名称执行工具"""
        tool = self.get_tool(name)
        if tool is None:
            raise KeyError(f"工具 '{name}' 未注册")
        return tool.execute(**kwargs)

    def get_all_schemas(self) -> list[dict]:
        """导出所有工具的 schema（用于注入 LLM prompt / function calling）"""
        return [tool.to_schema() for tool in self._tools.values()]

    def list_names(self) -> list[str]:
        """列出所有工具名"""
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools


# ========== 默认注册表工厂 ==========
def create_default_registry() -> ToolRegistry:
    """创建并返回一个包含所有内置工具的注册表"""
    registry = ToolRegistry()

    # 1. 计算器
    registry.register(
        Tool(
            name="calculator",
            description="对数学表达式进行求值，支持 + - * / // % ** 等运算。当需要精确计算时使用。",
            parameters={
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "数学表达式，例如 '(1+2)*3' 或 '2**10'",
                    }
                },
                "required": ["expression"],
            },
            executor_type="local",
            func=_tool_calculator,
        )
    )

    # 2. 搜索
    registry.register(
        Tool(
            name="search",
            description="根据关键词搜索信息（模拟搜索引擎）。当用户询问事实性问题时使用。",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词",
                    }
                },
                "required": ["query"],
            },
            executor_type="local",
            func=_tool_search,
        )
    )

    # # 3. 天气
    # registry.register(
    #     Tool(
    #         name="weather",
    #         description="查询指定城市的实时天气（模拟数据）。当用户询问天气时使用。",
    #         parameters={
    #             "type": "object",
    #             "properties": {
    #                 "city": {
    #                     "type": "string",
    #                     "description": "城市名称，例如 '北京'",
    #                 }
    #             },
    #             "required": ["city"],
    #         },
    #         executor_type="local",
    #         func=_tool_weather,
    #     )
    # )

    # 4. 待办管理
    registry.register(
        Tool(
            name="todo",
            description="管理待办事项，支持增/查/标记完成/删除。",
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["add", "list", "done", "delete"],
                        "description": "操作类型",
                    },
                    "content": {
                        "type": "string",
                        "description": "待办内容（action=add 时必填）",
                    },
                    "task_id": {
                        "type": "integer",
                        "description": "待办ID（action=done/delete 时必填）",
                    },
                },
                "required": ["action"],
            },
            executor_type="local",
            func=_tool_todo,
        )
    )

    # mcp接口接入
    registry_mcp_tools(registry)
    return registry


def registry_mcp_tools(registry: ToolRegistry) -> ToolRegistry:
    """返回所有 MCP 接口的工具"""
    # 修复1：合法变量接收MCP工具数组
    tool_list = mcp_client.get_mcp_tool_list()

    # 加打印日志，方便排查有没有进到循环
    logging.info(f"从MCP代理获取到工具总数：{len(tool_list)}")

    # 遍历循环注册
    for tool in tool_list:
        # 安全取值，避免key不存在报错
        tool_name = tool.get("name", "")
        tool_desc = tool.get("description", "")
        tool_params = tool.get("inputSchema", {})

        if not tool_name:
            continue

        logging.info(f"正在注册MCP工具：{tool_name}")

        registry.register(
            Tool(
                name=tool_name,
                description=tool_desc,
                parameters=tool_params,
                executor_type="mcp",
            )
        )

    return registry


# ========== 自测入口 ==========
if __name__ == "__main__":
    reg = create_default_registry()
    print("已注册工具:", reg.list_names())
    print(reg.execute("calculator", expression="(1+2)*3"))
    print(reg.execute("maps_weather", city="厦门"))
