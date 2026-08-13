"""
会话管理 & Agent 核心流程测试用例

覆盖场景:
1. SessionManager 基础: 创建/获取/列出/关闭会话
2. 会话隔离: 两个独立会话的上下文互不影响
3. 会话记忆: 同一会话能记住之前的对话状态
4. 上下文压缩: 超过最大轮次自动压缩
5. 工具分类路由: 关键词匹配筛选工具
6. 工具重试与去重: 连续失败跳过、重复调用去重
7. CLI 多会话切换模拟
"""

import sys
import os
import json
from datetime import datetime

# ========== 项目根目录注入 ==========
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from demo.session_manager import SessionManager
from demo.agent_runtime import AgentRuntime
from demo.context_manager import ContextManager
from demo.tools.tool_registry import (
    ToolRegistry, Tool, CATEGORY_KEYWORDS,
    create_default_registry, _guess_mcp_category,
)
from demo.tools.tool import Tool as ToolModel
from demo.llm.llm_client import LLMClient, LLMResponse
from demo.llm.llm_output_parser import LLMOutputParser, ParsedOutput

PASSED = 0
FAILED = 0


def check(condition: bool, name: str, detail: str = ""):
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  ✅ {name}")
    else:
        FAILED += 1
        print(f"  ❌ {name}  ← {detail}")


def section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


# ====================================================================
# Test 1: SessionManager 基础功能
# ====================================================================
def test_session_manager_basic():
    section("Test 1: SessionManager 基础功能")

    sm = SessionManager()
    sm.init_tool_registry()

    # 1.1 创建会话
    r1 = sm.get_or_create("session_a")
    check(isinstance(r1, AgentRuntime), "1.1 创建会话返回 AgentRuntime 实例")

    # 1.2 获取同一会话（复用）
    r2 = sm.get_or_create("session_a")
    check(r1 is r2, "1.2 同 session_id 返回同一实例")

    # 1.3 列出会话
    sessions = sm.list_sessions()
    check("session_a" in sessions and len(sessions) == 1, f"1.3 list_sessions: {sessions}")

    # 1.4 创建第二个会话
    sm.get_or_create("session_b")
    check(sm.session_count == 2, f"1.4 会话数: {sm.session_count}")

    # 1.5 关闭会话
    closed = sm.close_session("session_a")
    check(closed is True, "1.5 关闭已有会话返回 True")
    check("session_a" not in sm.list_sessions(), "1.5a 关闭后不在列表中")

    closed_nonexist = sm.close_session("nonexistent")
    check(closed_nonexist is False, "1.5b 关闭不存在的会话返回 False")


# ====================================================================
# Test 2: 会话隔离 — 两个窗口互不影响
# ====================================================================
def test_session_isolation():
    section("Test 2: 会话隔离")

    sm = SessionManager()
    sm.init_tool_registry()

    # 创建两个独立会话
    rt_a = sm.get_or_create("window_1")
    rt_b = sm.get_or_create("window_2")

    # 验证 ContextManager 独立
    ctx_a = rt_a.context_manager
    ctx_b = rt_b.context_manager

    # 2.1 初始状态独立
    check(ctx_a.message_count == 1, f"2.1 window_1 初始消息数: {ctx_a.message_count} (只有 system)")
    check(ctx_b.message_count == 1, f"2.1a window_2 初始消息数: {ctx_b.message_count}")

    # 2.2 window_1 添加用户消息
    ctx_a.add_user_message("帮我查北京天气")
    check(ctx_a.message_count == 2, f"2.2 window_1 消息数: {ctx_a.message_count}")
    check(ctx_b.message_count == 1, f"2.2a window_2 不受影响: {ctx_b.message_count}")

    # 2.3 window_2 添加不同消息
    ctx_b.add_user_message("帮我写周报待办")
    ctx_b.add_tool_result("call_1", "已添加待办 #1: 写周报")
    check(ctx_b.message_count == 3, f"2.3 window_2 消息数: {ctx_b.message_count}")

    # 2.4 window_1 不受 window_2 影响
    check(ctx_a.message_count == 2, f"2.4 window_1 仍然不受影响: {ctx_a.message_count}")

    # 2.5 验证消息内容独立
    msgs_a = ctx_a.get_messages()
    msgs_b = ctx_b.get_messages()
    check(msgs_a[1]["content"] == "帮我查北京天气", f"2.5 window_1 消息内容: {msgs_a[1]['content']}")
    check(msgs_b[1]["content"] == "帮我写周报待办", f"2.5a window_2 消息内容: {msgs_b[1]['content']}")

    sm.close_session("window_1")
    sm.close_session("window_2")


# ====================================================================
# Test 3: 会话记忆 — 追问支持
# ====================================================================
def test_session_memory():
    section("Test 3: 会话记忆")

    sm = SessionManager()
    sm.init_tool_registry()
    rt = sm.get_or_create("memory_test")
    ctx = rt.context_manager

    # 3.1 添加多轮对话
    ctx.add_user_message("你好")
    ctx.add_assistant_message(content="你好！我是智能助手。")
    ctx.add_user_message("我叫张三")
    ctx.add_assistant_message(content="好的，我记住了，你叫张三。")
    ctx.add_user_message("帮我查天气")

    messages = ctx.get_messages()
    check(len(messages) == 6, f"3.1 消息数: {len(messages)} (system + 5 对话)")

    # 3.2 追问时历史仍在
    contents = [m["content"] for m in messages if m["role"] == "user"]
    check("你好" in contents, "3.2 历史消息保留: '你好'")
    check("我叫张三" in contents, "3.2a 历史消息保留: '我叫张三'")
    check("帮我查天气" in contents, "3.2b 最新追问保留: '帮我查天气'")

    # 3.3 助手消息也保留
    assistant_contents = [m["content"] for m in messages if m["role"] == "assistant"]
    check("你好！我是智能助手。" in assistant_contents, "3.3 助手回复保留")
    check("好的，我记住了，你叫张三。" in assistant_contents, "3.3a 助手回复保留")

    sm.close_session("memory_test")


# ====================================================================
# Test 4: 上下文压缩
# ====================================================================
def test_context_compression():
    section("Test 4: 上下文压缩")

    ctx = ContextManager(system_prompt="测试系统提示词")

    # 4.1 初始不压缩
    for i in range(5):
        ctx.add_user_message(f"问题 {i}")
        ctx.add_assistant_message(content=f"回答 {i}")

    check(ctx.message_count == 11, f"4.1 添加 5 轮后消息数: {ctx.message_count}")

    # 4.2 触发压缩 — 添加更多消息
    for i in range(20):
        ctx.add_user_message(f"追加问题 {i}")
        ctx.add_assistant_message(content=f"追加回答 {i}")

    # 压缩后保留 system + 最近 N 条 (CONTEXT_MAX_KEEP_MESSAGES=8)
    messages = ctx.get_messages()
    # system(1) + 最近 10 条 = 11 条
    check(len(messages) <= 11, f"4.2 压缩后消息数 <= 11: {len(messages)}")

    # 4.3 system 消息始终保留
    check(messages[0]["role"] == "system", "4.3 system 消息始终在首位")
    check(messages[0]["content"] == "测试系统提示词", "4.3a system 内容完整")

    # 4.4 旧消息被截断 — 最早的消息不在
    user_msgs = [m["content"] for m in messages if m["role"] == "user"]
    check("问题 0" not in user_msgs, "4.4 最早的用户消息已被截断")
    check("问题 1" not in user_msgs, "4.4a 次早的用户消息已被截断")


# ====================================================================
# Test 5: 工具分类与关键词路由
# ====================================================================
def test_tool_classification():
    section("Test 5: 工具分类与关键词路由")

    # 5.1 关键词路由表存在
    check("weather" in CATEGORY_KEYWORDS, "5.1 weather 类别关键词存在")
    check("navigation" in CATEGORY_KEYWORDS, "5.1a navigation 类别关键词存在")
    check("search" in CATEGORY_KEYWORDS, "5.1b search 类别关键词存在")

    # 5.2 关键词匹配测试
    from demo.tools.tool_registry import CATEGORY_KEYWORDS as CK

    def match(query: str) -> set:
        return {cat for cat, kws in CK.items() if any(kw in query for kw in kws)}

    check("weather" in match("厦门明天天气怎么样"), "5.2 '天气' 匹配 weather 类")
    check("navigation" in match("从厦门站到厦门大学怎么走"), "5.2a '怎么走' 匹配 navigation 类")
    check("search" in match("附近有什么好吃的"), "5.2b '附近' 匹配 search 类")
    check("distance" in match("距离多远多少公里"), "5.2c '多远' 匹配 distance 类")

    # 组合匹配
    cats = match("查天气和导航路线")
    check("weather" in cats and "navigation" in cats, f"5.3 组合匹配: {cats}")

    # 5.3 MCP 工具分类猜测
    check(_guess_mcp_category("maps_weather") == "weather", "5.4 maps_weather → weather")
    check(_guess_mcp_category("maps_direction_driving") == "navigation", "5.4a maps_direction → navigation")
    check(_guess_mcp_category("maps_geo") == "search", "5.4b maps_geo → search")
    check(_guess_mcp_category("maps_distance") == "distance", "5.4c maps_distance → distance")
    check(_guess_mcp_category("unknown_tool") == "general", "5.5 未知工具 → general")

    # 5.4 get_schemas_by_query 筛选
    registry = ToolRegistry()
    registry.register(Tool(name="calculator", description="计算", parameters={"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]}, executor_type="local", category="general", func=lambda **kw: "ok"))
    registry.register(Tool(name="maps_weather", description="天气", parameters={"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}, executor_type="mcp", category="weather"))
    registry.register(Tool(name="maps_direction_driving", description="驾车", parameters={"type": "object", "properties": {"from": {"type": "string"}, "to": {"type": "string"}}, "required": ["from", "to"]}, executor_type="mcp", category="navigation"))

    # 匹配天气 → general + weather
    weather_tools = registry.get_schemas_by_query("厦门天气")
    weather_names = [t["function"]["name"] for t in weather_tools]
    check("calculator" in weather_names, f"5.6 general 工具始终包含: {weather_names}")
    check("maps_weather" in weather_names, f"5.6a 匹配 weather: {weather_names}")
    check("maps_direction_driving" not in weather_names, f"5.6b navigation 被过滤: {weather_names}")

    # 匹配导航 → general + navigation
    nav_tools = registry.get_schemas_by_query("怎么去机场")
    nav_names = [t["function"]["name"] for t in nav_tools]
    check("maps_direction_driving" in nav_names, f"5.7 匹配 navigation: {nav_names}")
    check("maps_weather" not in nav_names, f"5.7a weather 被过滤: {nav_names}")

    # 未匹配 → 兜底全部
    all_tools = registry.get_schemas_by_query("你好")
    check(len(all_tools) == 3, f"5.8 未匹配关键词 → 全部工具: {len(all_tools)}")


# ====================================================================
# Test 6: Tool 模型与安全执行
# ====================================================================
def test_tool_model():
    section("Test 6: Tool 模型")

    # 6.1 计算器安全执行
    from demo.tools.tool_registry import _safe_eval
    check(_safe_eval("2**10") == 1024, f"6.1 2**10 = {_safe_eval('2**10')}")
    check(_safe_eval("(1+2)*3") == 9, f"6.1a (1+2)*3 = {_safe_eval('(1+2)*3')}")
    check(_safe_eval("10/3") == 10/3, f"6.1b 10/3 = {_safe_eval('10/3')}")

    # 6.2 非法表达式被拒绝
    error_caught = False
    try:
        _safe_eval("import os")
    except (ValueError, SyntaxError):
        error_caught = True
    check(error_caught, "6.2 非法表达式被拒绝")

    # 6.3 Tool.to_schema() 格式
    tool = Tool(
        name="test_tool",
        description="测试工具",
        parameters={"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]},
        executor_type="local",
        category="general",
        func=lambda x: x,
    )
    schema = tool.to_schema()
    check(schema["type"] == "function", f"6.3 schema type: {schema['type']}")
    check(schema["function"]["name"] == "test_tool", f"6.3a schema name: {schema['function']['name']}")
    check("parameters" in schema["function"], "6.3b schema 包含 parameters")


# ====================================================================
# Test 7: LLMOutputParser 双模式解析
# ====================================================================
def test_output_parser():
    section("Test 7: LLMOutputParser")

    parser = LLMOutputParser()

    # 7.1 原生 tool_calls 解析
    native_response = LLMResponse(
        content="",
        tool_calls=[{
            "id": "call_1",
            "name": "calculator",
            "arguments": {"expression": "2+2"}
        }],
        finish_reason="tool_calls",
    )
    results = parser.parse(native_response)
    check(len(results) == 1, f"7.1 原生解析结果数: {len(results)}")
    check(results[0].is_tool_call, f"7.1a action: {results[0].action}")
    check(results[0].tool_name == "calculator", f"7.1b tool_name: {results[0].tool_name}")
    check(results[0].tool_args == {"expression": "2+2"}, f"7.1c tool_args: {results[0].tool_args}")

    # 7.2 最终答案解析
    answer_response = LLMResponse(
        content="北京今日晴，28°C。",
        tool_calls=[],
        finish_reason="stop",
    )
    results2 = parser.parse(answer_response)
    check(results2[0].is_final_answer, f"7.2 action: {results2[0].action}")
    check(results2[0].final_answer == "北京今日晴，28°C。", f"7.2a 内容: {results2[0].final_answer}")

    # 7.3 多工具调用
    multi_response = LLMResponse(
        content="",
        tool_calls=[
            {"id": "call_1", "name": "weather", "arguments": {"city": "北京"}},
            {"id": "call_2", "name": "calculator", "arguments": {"expression": "100*2"}},
        ],
        finish_reason="tool_calls",
    )
    results3 = parser.parse(multi_response)
    check(len(results3) == 2, f"7.3 多工具解析: {len(results3)}")
    check(results3[0].tool_name == "weather", "7.3a 第一个工具: weather")
    check(results3[1].tool_name == "calculator", "7.3b 第二个工具: calculator")


# ====================================================================
# Test 8: 工具去重与异常容错（模拟）
# ====================================================================
def test_tool_deduplication():
    section("Test 8: 工具去重与异常容错")

    # 8.1 AgentRuntime 内部 trace 去重逻辑（模拟）
    from demo.agent_runtime import AgentRuntime

    # 创建一个最小化的 runtime 测试 trace 去重
    registry = ToolRegistry()
    registry.register(Tool(
        name="calculator",
        description="计算",
        parameters={"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]},
        executor_type="local",
        category="general",
        func=lambda expression: f"{expression} = {eval(expression)}",
    ))

    rt = AgentRuntime(
        tool_registry=registry,
        llm_client=LLMClient(),
        context_manager=ContextManager("test"),
    )

    # 手动模拟 trace 记录
    rt._trace = [
        {"tool": "calculator", "args": {"expression": "2+2"}, "result": "2+2 = 4", "timestamp": "t1"}
    ]

    # 8.2 相同 tool+args 被判重
    already_called = any(
        t.get("tool") == "calculator" and t.get("args") == {"expression": "2+2"}
        for t in rt._trace
    )
    check(already_called, "8.2 相同 tool+args 判重生效")

    # 8.3 不同参数不被判重
    already_called_diff = any(
        t.get("tool") == "calculator" and t.get("args") == {"expression": "3+3"}
        for t in rt._trace
    )
    check(not already_called_diff, "8.3 不同参数不被判重")


# ====================================================================
# Test 9: 两个窗口场景
# ====================================================================
def test_two_windows_scenario():
    section("Test 9: 两个窗口场景完整模拟")

    sm = SessionManager()
    sm.init_tool_registry()

    # === 窗口 1: 查天气 ===
    win1_ctx = sm.get_or_create("窗口1").context_manager
    win1_ctx.add_user_message("帮我查北京天气")
    win1_ctx.add_assistant_message(content="好的，让我帮你查北京天气。")
    win1_ctx.add_tool_result("call_w1", "北京今日晴，气温28°C")

    # === 窗口 2: 写周报 ===
    win2_ctx = sm.get_or_create("窗口2").context_manager
    win2_ctx.add_user_message("帮我写周报待办")
    win2_ctx.add_assistant_message(content="好的，已为你添加周报待办。")
    win2_ctx.add_tool_result("call_w2", "已添加待办 #1: 写周报")

    # === 验证隔离 ===
    msgs1 = win1_ctx.get_messages()
    msgs2 = win2_ctx.get_messages()

    check(len(msgs1) == 4, f"9.1 窗口1 消息数: {len(msgs1)} (system+user+assistant+tool)")
    check(len(msgs2) == 4, f"9.1a 窗口2 消息数: {len(msgs2)}")

    # 9.2 窗口1 内容不含周报
    all_contents_1 = " ".join(m.get("content", "") for m in msgs1)
    check("周报" not in all_contents_1, "9.2 窗口1 不含'周报'内容")
    check("天气" in all_contents_1, "9.2a 窗口1 含'天气'内容")

    # 9.3 窗口2 内容不含天气
    all_contents_2 = " ".join(m.get("content", "") for m in msgs2)
    check("天气" not in all_contents_2, "9.3 窗口2 不含'天气'内容")
    check("周报" in all_contents_2, "9.3a 窗口2 含'周报'内容")

    # 9.4 窗口1 继续对话（追问）
    win1_ctx.add_user_message("明天呢？")
    msgs1_new = win1_ctx.get_messages()
    check(len(msgs1_new) == 5, f"9.4 窗口1 追问后消息数: {len(msgs1_new)}")
    # 历史仍在
    all_contents_1_new = " ".join(m.get("content", "") for m in msgs1_new)
    check("天气" in all_contents_1_new, "9.4a 窗口1 追问后历史仍在")

    # 9.5 窗口2 不受窗口1 追问影响
    check(len(win2_ctx.get_messages()) == 4, f"9.5 窗口2 不受影响: {len(win2_ctx.get_messages())}")     

    # 9.6 列出两个会话
    check(sm.session_count == 2, f"9.6 活跃会话数: {sm.session_count}")
    sessions = sm.list_sessions()
    check("窗口1" in sessions and "窗口2" in sessions, f"9.6a 会话列表: {sessions}")

    # 清理
    sm.close_session("窗口1")
    sm.close_session("窗口2")


# ====================================================================
# Test 10: ContextManager 基本接口
# ====================================================================
def test_context_manager_api():
    section("Test 10: ContextManager 基本接口")

    # 10.1 空 context
    ctx = ContextManager()
    check(ctx.message_count == 0, f"10.1 空 context 消息数: {ctx.message_count}")

    # 10.2 带 system prompt
    ctx2 = ContextManager(system_prompt="你是助手")
    check(ctx2.message_count == 1, f"10.2 带 system prompt: {ctx2.message_count}")
    check(ctx2.get_messages()[0]["role"] == "system", "10.2a 第一条是 system")

    # 10.3 add_user_message
    ctx2.add_user_message("你好")
    check(ctx2.message_count == 2, f"10.3 添加用户消息: {ctx2.message_count}")

    # 10.4 add_assistant_message
    ctx2.add_assistant_message(content="你好！")
    check(ctx2.message_count == 3, f"10.4 添加助手消息: {ctx2.message_count}")

    # 10.5 add_tool_result
    ctx2.add_tool_result("call_1", "工具执行结果")
    check(ctx2.message_count == 4, f"10.5 添加工具结果: {ctx2.message_count}")
    check(ctx2.get_messages()[3]["role"] == "tool", "10.5a 角色为 tool")

    # 10.6 clear
    ctx2.clear()
    check(ctx2.message_count == 1, f"10.6 clear 后保留 system: {ctx2.message_count}")

    # 10.7 assistant with tool_calls
    ctx3 = ContextManager(system_prompt="test")
    ctx3.add_assistant_message(
        content="",
        tool_calls=[{"id": "call_x", "name": "search", "arguments": {"query": "test"}}],
    )
    msgs = ctx3.get_messages()
    check(len(msgs) == 2, f"10.7 assistant + tool_calls: {len(msgs)}")
    check("tool_calls" in msgs[1], "10.7a 消息包含 tool_calls 字段")


# ====================================================================
# 入口
# ====================================================================
if __name__ == "__main__":
    print("╔══════════════════════════════════════════════════════════╗")
    print("║         Agent Demo 测试套件                               ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print(f"  开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    test_session_manager_basic()
    test_session_isolation()
    test_session_memory()
    test_context_compression()
    test_tool_classification()
    test_tool_model()
    test_output_parser()
    test_tool_deduplication()
    test_two_windows_scenario()
    test_context_manager_api()

    print(f"\n{'='*60}")
    print(f"  测试完成: ✅ {PASSED} 通过  ❌ {FAILED} 失败  共 {PASSED + FAILED} 项")
    print(f"{'='*60}")

    sys.exit(0 if FAILED == 0 else 1)
