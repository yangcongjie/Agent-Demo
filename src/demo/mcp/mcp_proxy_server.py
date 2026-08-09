"""
MCP 2.0 客户端代理服务
- 通过 fastmcp 连接阿里云百炼 MCP 服务（高德地图等）
- 对外暴露 HTTP API，供 AgentRuntime 调用

前置条件：
1. 安装 fastmcp: pip install fastmcp
2. 在阿里云百炼 MCP 广场开通高德地图服务：
   https://bailian.console.aliyun.com/?tab=mcp#/mcp-market/detail/amap-maps
3. 确保 DASHSCOPE_API_KEY 有百炼平台访问权限
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

import logging

# 当前模块日志生效
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# 阿里云百炼 MCP 服务地址（高德地图）
MCP_SERVER_URL = "https://dashscope.aliyuncs.com/api/v1/mcps/amap-maps/mcp"
DASHSCOPE_API_KEY = "sk-d00a574456bb44c1977072fdc0244ef9"

# 全局 MCP 客户端
_client: Optional[Client] = None


class CallToolRequest(BaseModel):
    tool_name: str
    arguments: dict


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI 生命周期：启动时连接 MCP Server，关闭时释放"""
    global _client

    logging.info(f"正在连接百炼 MCP 服务: {MCP_SERVER_URL}")

    # fastmcp 2.0 正确写法：通过 StreamableHttpTransport 传入 headers
    transport = StreamableHttpTransport(
        url=MCP_SERVER_URL,
        headers={"Authorization": f"Bearer {DASHSCOPE_API_KEY}"},
        # 注意：sse_read_timeout 已废弃，用 read_timeout_seconds 替代
        sse_read_timeout=30,
    )

    _client = Client(transport=transport)

    try:
        # 测试连接：尝试列出工具
        async with _client:
            tools = await _client.list_tools()
            tool_names = [t.name for t in tools]
            logging.info(f"百炼 MCP 连接成功，可用工具: {tool_names}")
    except Exception as e:
        logging.error(f"百炼 MCP 连接失败: {e}")
        logging.error("请检查：1.是否已在百炼平台开通高德地图 MCP 服务  2.API Key 是否有效")
        _client = None

    yield

    _client = None
    logging.info("MCP 连接已关闭")


app = FastAPI(title="MCP 2.0 HTTP Proxy", lifespan=lifespan)


@app.get("/api/tools")
async def get_all_tools():
    """获取 MCP Server 暴露的所有工具"""
    if not _client:
        return {"error": "MCP 客户端未初始化，请检查日志"}
    try:
        async with _client as client:
            tools = await client.list_tools()
            return {"tools": [{"name": t.name, "description": t.description,
                               "inputSchema": t.inputSchema} for t in tools]}
    except Exception as e:
        logging.error(f"获取工具列表失败: {e}")
        return {"error": str(e)}


@app.post("/api/call")
async def call_mcp_tool(req: CallToolRequest):
    """调用指定的 MCP 工具"""
    if not _client:
        return {"result": [], "error": "MCP 客户端未初始化"}
    tool_name = req.tool_name
    arguments = req.arguments if isinstance(req.arguments, dict) else {}
    logging.info(f"收到调用请求 tool_name={tool_name}, arguments={arguments}")
    try:
        async with _client as client:
            # 返回单个对象，不是列表
            result_obj = await client.call_tool(tool_name, arguments=arguments)

            logging.info(f"MCP工具调用成功，返回结果: {result_obj}")
            return result_obj

    except Exception as e:
        err_msg = str(e)
        logging.error(f"调用工具 {req.tool_name} 失败: {err_msg}")
        return {"result": [], "error": err_msg}

@app.get("/api/health")
async def health():
    """健康检查"""
    return {"status": "ok", "mcp_server_url": MCP_SERVER_URL}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8002)
