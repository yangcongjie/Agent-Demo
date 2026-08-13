# Agent-Demo

演示Demo，支持工具调用（Function Calling + MCP 2.0）、多会话隔离、上下文压缩、关键词工具路由。

## 目录

- [快速开始](#快速开始)
- [系统设计](#系统设计)
- [Memory 召回时机与放置方式](#memory-召回时机与放置方式)
- [项目结构](#项目结构)

---

## 快速开始

### 环境要求

Agent服务环境
- Python 3.9+

MCP代理服务环境
- Python 3.10+

### 安装

```bash
# 1. 安装项目包（可编辑模式）
pip install -e .

# 2. 安装 MCP 代理依赖
pip install fastapi uvicorn mcp requests pydantic-settings

# 3. 配置 API Key
#    在项目根目录创建 .env 文件（可选，也可直接用 config.py 中的默认值）
echo LLM_API_KEY=your-api-key-here > .env
```

### 运行

**方式一：本地控制台交互模式**

```bash
python -m demo.main
```

```
===== Agent 控制台交互 =====
命令:
  session:xxx   切换/创建会话 (如 session:window1)
  sessions      查看所有会话
  quit / exit   退出程序
当前会话: default

[default] 用户：帮我查厦门天气
智能助手：厦门今日多云，气温34°C...

[default] 用户：session:window1
已切换到会话: window1

[window1] 用户：我叫什么？     ← 独立上下文，不知道 default 的对话
智能助手：抱歉，我不知道你的名字。
```

**方式二：FastAPI 服务模式（支持多窗口并发）**

```bash
python -m demo.main --api          # 默认 8000 端口
python -m demo.main --api --port=8080
```

调用示例：

```bash
# 窗口 A
curl -X POST http://localhost:8000/api/chat \
  -H "X-Token: 1125" -H "Content-Type: application/json" \
  -d '{"query": "我叫张三", "session_id": "windowA"}'

# 窗口 B（互不影响）
curl -X POST http://localhost:8000/api/chat \
  -H "X-Token: 1125" -H "Content-Type: application/json" \
  -d '{"query": "我叫什么？", "session_id": "windowB"}'
# → "抱歉，我不知道你的名字"

# 窗口 A 追问（记得上下文）
curl -X POST http://localhost:8000/api/chat \
  -H "X-Token: 1125" -H "Content-Type: application/json" \
  -d '{"query": "我叫什么？", "session_id": "windowA"}'
# → "你叫张三"

# 查看所有会话
curl http://localhost:8000/api/sessions -H "X-Token: 1125"

# 关闭会话
curl -X DELETE http://localhost:8000/api/sessions/windowA -H "X-Token: 1125"
```

### 运行测试

```bash
python -m tests.test_agent
```

---

## 系统设计

### 架构总览

```
用户输入
   │
   ▼
┌───────────────┐     ┌──────────────────┐     ┌──────────────────┐
│   main.py     │────▶│  SessionManager   │────▶│  ContextManager  │
│  (CLI / API)  │     │  (会话隔离)       │     │  (上下文管理)     │
└───────────────┘     └──────────────────┘     └──────────────────┘
   │                        │                          │
   │                        ▼                          │
   │               ┌──────────────────┐               │
   │               │  AgentRuntime    │◀──────────────┘
   │               │  (核心循环)      │
   │               └──────────────────┘
   │                        │
   │          ┌─────────────┼─────────────┐
   │          ▼             ▼             ▼
   │   ┌────────────┐ ┌──────────┐ ┌──────────────┐
   │   │ LLMClient  │ │ Parser   │ │ ToolRegistry │
   │   │ (API调用)  │ │ (输出解析)│ │ (工具注册)   │
   │   └────────────┘ └──────────┘ └──────────────┘
   │                                     │
   │                          ┌──────────┼──────────┐
   │                          ▼          ▼          ▼
   │                     本地工具     MCP 代理    高德地图
   │                  (calculator   (HTTP 转发   MCP Server
   │                   search todo)  8002端口)   百炼平台)
```

### 核心循环（AgentRuntime）

Agent 的 think-act-observe 循环，在 [agent_runtime.py](src/demo/agent_runtime.py) 中实现：

```
while loop < MAX_LOOP(7):
    1. 从 ContextManager 取消息 + 工具 schema → 调用 LLM
    2. Parser 解析 LLM 输出
       ├── tool_call  → 执行工具 → 结果回填 context → continue
       ├── final_answer → 返回给用户 → break
       └── 空输出 → 重试
    3. 工具异常：错误信息也喂回 LLM，让模型自行处理
    4. 超过 MAX_LOOP → 返回兜底回答
```

### 组件职责

| 组件 | 文件 | 职责 |
|------|------|------|
| **AgentRuntime** | `agent_runtime.py` | 核心循环：调用 LLM → 解析 → 工具执行 → 结果回填 |
| **SessionManager** | `session_manager.py` | 多会话管理，共享无状态组件，隔离 ContextManager |
| **ContextManager** | `context_manager.py` | 消息历史管理、轮次压缩、追问支持 |
| **LLMClient** | `llm/llm_client.py` | 封装 LLM API 调用，支持 Function Calling |
| **LLMOutputParser** | `llm/llm_output_parser.py` | 双模式解析：原生 tool_calls + 文本降级 |
| **ToolRegistry** | `tools/tool_registry.py` | 工具注册 + 关键词路由筛选 |
| **MCP Proxy** | `mcp/mcp_proxy_server.py` | MCP 2.0 代理，转发到高德地图 MCP Server |

### 多会话隔离设计

```
SessionManager（单例）
├── 共享组件（无状态，只创建一次）
│   ├── LLMClient        ← API 调用无状态，可安全共享
│   ├── ToolRegistry      ← 工具注册表无状态，可安全共享
│   └── LLMOutputParser   ← 解析器无状态，可安全共享
│
├── sessions["windowA"] → AgentRuntime → ContextManager (独立对话历史)
├── sessions["windowB"] → AgentRuntime → ContextManager (独立对话历史)
└── sessions["windowC"] → AgentRuntime → ContextManager (独立对话历史)
```

关键原则：**无状态组件共享，有状态组件（ContextManager）隔离**。

### 工具分类路由

18 个工具每次全量传入 LLM 会消耗大量 token。通过关键词路由按需筛选：

```
用户输入: "厦门明天天气"
  → 匹配关键词: "天气" → weather 类
  → 发送: calculator + search + todo + maps_weather = 4 个工具（原 18 个）
  → token 节省 ~70%
```

| 类别 | 始终包含 | 关键词触发 |
|------|:--------:|-----------|
| general | ✅ | calculator, search, todo |
| weather | | 天气、气温、下雨、晴... |
| navigation | | 导航、路线、怎么走、打车... |
| search | | 搜索、附近、找、地址... |
| distance | | 距离、多远、多少公里 |
| map | | 地图、行程、路书 |

未匹配任何关键词时兜底发送全部工具，保证不丢能力。

### 异常容错链路

```
工具执行
  ├── 成功 → 结果喂回 LLM
  ├── 失败 → 自动重试 3 次（递增等待 0.5s → 1.0s）
  │         ├── 重试成功 → 结果喂回 LLM
  │         └── 3 次都失败 → 错误文本喂回 LLM，让模型换策略
  └── 重复调用检测 → 相同工具+参数跳过，复用历史结果
```

---

## Memory 召回时机与放置方式

### Memory 的本质

本项目中的 "Memory" 指的是 **ContextManager 管理的对话历史**。LLM 本身无状态，每次调用都是独立的，"记忆" 完全依赖把历史消息重新传入 messages 数组。

### 召回时机

**召回发生在每次 LLM 调用前**（[agent_runtime.py L63-65](src/demo/agent_runtime.py#L63-L65)）：

```python
response = self.llm_client.chat(
    messages=self.context_manager.get_messages(),  # ← 这里召回全部历史
    tools=filtered_tools,
)
```

### 放置方式

ContextManager 内部维护一个 `_messages` 列表，结构遵循 OpenAI Chat API 格式：

```
_messages = [
    {"role": "system",    "content": "你是智能助手..."},        # ① 系统提示（始终首位）
    {"role": "user",      "content": "帮我查厦门天气"},          # ② 用户消息
    {"role": "assistant", "content": "", "tool_calls": [...]},  # ③ LLM 决定调工具
    {"role": "tool",      "tool_call_id": "call_1", "content": "厦门多云34°C"}, # ④ 工具结果
    {"role": "assistant", "content": "厦门今日多云，气温34°C"},  # ⑤ LLM 综合回答
    {"role": "user",      "content": "明天呢？"},               # ⑥ 追问（自然引用上文）
]
```

| 消息类型 | 写入方法 | 写入时机 |
|---------|---------|---------|
| system | `__init__()` 初始化 | 创建会话时，一次写入 |
| user | `add_user_message()` | 每轮循环开始，用户输入后立即写入 |
| assistant (含 tool_calls) | `add_assistant_message()` | LLM 返回 tool_calls 后，工具执行前写入 |
| tool (工具结果) | `add_tool_result()` | 工具执行完成后立即写入 |
| assistant (最终答案) | `add_assistant_message()` | LLM 返回 final_answer 后写入 |

### 压缩策略

当消息数超过 `CONTEXT_MAX_KEEP_MESSAGES`（默认 10 条）时触发压缩：

```
压缩前 (15 条):
[system, u1, a1, t1, a2, u2, a3, t2, a4, u3, a5, t3, a6, u4, a7]
                                                    ↑ 保留最近 10 条

压缩后 (11 条):
[system, a3, t2, a4, u3, a5, t3, a6, u4, a7]
  ↑ 始终保留            ↑ 最近 10 条非 system 消息
```

- **system 消息**：始终保留，不参与计数
- **非 system 消息**：超过阈值时只保留最近 N 条
- **触发时机**：`get_messages()` 每次被调用时自动检查

### 多会话下的 Memory 隔离

每个 `session_id` 对应独立的 `ContextManager` 实例，消息列表完全隔离：

```
SessionManager
├── "windowA" → ContextManager._messages = [system, u1:"我叫张三", a1:"好的", ...]
├── "windowB" → ContextManager._messages = [system, u1:"帮我写周报", a1:"已添加", ...]
└── "windowC" → ContextManager._messages = [system]  ← 新会话，空历史
```

窗口 A 的 Memory 不会出现在窗口 B 的 `get_messages()` 返回中，实现完全隔离。

---

## 项目结构

```
demo/
├── pyproject.toml              # 项目元数据 + 依赖声明
├── .env                        # 环境变量（API Key，不提交 git）
├── README.md
├── src/
│   └── demo/                   # Python 包根
│       ├── __init__.py
│       ├── config.py           # pydantic-settings 配置中心
│       ├── main.py             # 入口（CLI + FastAPI）
│       ├── agent_runtime.py    # Agent 核心循环
│       ├── context_manager.py  # 上下文管理 + 压缩
│       ├── session_manager.py  # 多会话管理
│       ├── logger_config.py    # 日志配置（console + file）
│       ├── llm/
│       │   ├── llm_client.py        # LLM API 客户端
│       │   └── llm_output_parser.py # 输出解析器（双模式）
│       ├── tools/
│       │   ├── tool.py              # Tool 模型 + 重试机制
│       │   └── tool_registry.py     # 工具注册 + 关键词路由
│       └── mcp/
│           ├── mcp_client.py        # MCP 工具调用客户端
│           ├── mcp_proxy_server.py  # MCP 2.0 代理服务
│           └── start_mcp.py         # MCP 代理启动/停止
├── tests/
│   └── test_agent.py          # 测试套件（10 组 50+ 断言）
└── logs/
    └── agent.log              # 运行日志
```
