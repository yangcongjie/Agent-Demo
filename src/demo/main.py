import logging
import sys
from typing import Optional
from fastapi import FastAPI, Depends, HTTPException, Query, Header
from pydantic import BaseModel
import uvicorn

# 你的原有内部导入（路径不动）
from demo.logger_config import setup_logging
from demo.mcp.start_mcp import start_mcp_proxy, stop_mcp_proxy
from demo.session_manager import SessionManager

# ===================== 全局配置 =====================
logger = logging.getLogger(__name__)
# 外网访问密钥，自行修改一串复杂字符
API_ACCESS_TOKEN = "1125"
# 全局会话管理器
session_manager: Optional[SessionManager] = None

# FastAPI 应用实例
app = FastAPI(
    title="杨聪杰的演示Demo",
    description="手动实现Agent Runtime，提供本地工具和高德地图MCP服务",
    version="1.0.0"
)

# ===================== 鉴权工具 =====================
def verify_access_token(x_token: str = Header(..., description="请求头携带Token")):
    if x_token != API_ACCESS_TOKEN:
        raise HTTPException(status_code=401, detail="Token 校验失败，禁止访问")
    return True

# ===================== 请求模型 =====================
class ChatPostRequest(BaseModel):
    query: str
    session_id: str = "default"  # 会话ID，不同窗口传不同值即可隔离

# ===================== API 接口 =====================

@app.post("/api/chat", dependencies=[Depends(verify_access_token)], summary="对话接口")
def chat_post_normal(req: ChatPostRequest):
    global session_manager
    if session_manager is None:
        raise HTTPException(status_code=500, detail="会话管理器未初始化")

    # 按 session_id 获取或创建独立的 AgentRuntime
    runtime = session_manager.get_or_create(req.session_id)
    reply = runtime.run(req.query)
    return {
        "session_id": req.session_id,
        "user_query": req.query,
        "answer": reply,
        "trace_logs": runtime.trace
    }

# 健康检测接口
@app.get("/health", summary="服务健康检查")
def health_check():
    return {
        "service_status": "running",
        "session_manager_ready": session_manager is not None,
        "active_sessions": session_manager.session_count if session_manager else 0,
    }

# 会话管理接口
@app.get("/api/sessions", dependencies=[Depends(verify_access_token)], summary="查看所有会话")
def list_sessions():
    if session_manager is None:
        raise HTTPException(status_code=500, detail="会话管理器未初始化")
    return {
        "sessions": session_manager.list_sessions(),
        "count": session_manager.session_count,
    }

@app.delete("/api/sessions/{session_id}", dependencies=[Depends(verify_access_token)], summary="关闭指定会话")
def close_session(session_id: str):
    if session_manager is None:
        raise HTTPException(status_code=500, detail="会话管理器未初始化")
    if session_manager.close_session(session_id):
        return {"message": f"会话 '{session_id}' 已关闭"}
    raise HTTPException(status_code=404, detail=f"会话 '{session_id}' 不存在")

# ===================== 两种启动模式函数 =====================
#本地控制台调试
def run_console_interactive():
    global session_manager
    setup_logging()
    logger.info("日志加载完成")

    # 初始化会话管理器 + MCP内部代理
    session_manager = SessionManager()
    logger.info("会话管理器初始化完成")
    start_mcp_proxy()
    session_manager.init_tool_registry()  # MCP 代理启动后再初始化工具
    logger.info("MCP中转代理启动完成，工具注册表初始化完成")

    #默认会话ID
    current_session = "default"

    try:
        while True:
            # 必须传入一个sessionID，否则会话切换失败
            user_input = input(f"[{current_session}] 用户：").strip()
            if not user_input:
                continue
            if user_input.lower() in ("quit", "exit", "q"):
                logger.info("退出")
                break

            # 切换会话
            if user_input.startswith("session:"):
                current_session = user_input.split(":", 1)[1].strip()
                session_manager.get_or_create(current_session)
                print(f"已切换到会话: {current_session}\n")
                continue

            # 查看所有会话
            if user_input == "sessions":
                sessions = session_manager.list_sessions()
                print(f"活跃会话 ({len(sessions)}): {sessions}\n")
                continue

            logger.info(f"用户输入内容：{user_input}")
            runtime = session_manager.get_or_create(current_session)
            result = runtime.run(user_input)
            print(f"\n智能助手：{result}\n")

            # 打印工具调用Trace日志
            if runtime.trace:
                print("----- 本次调用链路 Trace -----")
                for item in runtime.trace:
                    tool_name = item.get("tool", "")
                    args = item.get("args", {})
                    output = item.get("result") or item.get("error")
                    print(f"工具：{tool_name} | 参数：{args} | 返回：{output}")
                print("-" * 40 + "\n")

    finally:
        # 无论正常退出还是异常崩溃，都关闭MCP代理
        stop_mcp_proxy()
        logger.info("MCP中转代理已安全关闭，程序退出")


def run_fastapi_server(api_host: str = "0.0.0.0", api_port: int = 8000):
    """启动FastAPI接口服务（用于ngrok外网穿透）"""
    global session_manager
    setup_logging()
    logger.info("日志加载完成")

    # 全局初始化会话管理器和MCP代理
    session_manager = SessionManager()
    logger.info("会话管理器初始化完成")
    start_mcp_proxy()
    logger.info("MCP中转代理启动完成")
    session_manager.init_tool_registry()
    logger.info(f"工具注册表初始化完成，共 {len(session_manager._tool_registry)} 个工具")
    logger.info(f"服务地址： {api_host}:{api_port}")

    try:
        # 启动uvicorn服务
        uvicorn.run(
            app=app,
            host=api_host,
            port=api_port,
            reload=False,
            log_level="info"
        )
    finally:
        # 服务停止后销毁MCP代理
        stop_mcp_proxy()
        logger.info("API服务已停止，MCP代理关闭")

# ===================== 程序入口 =====================
if __name__ == "__main__":
    # 1. 默认控制台模式：python main.py
    # 2. API默认8000端口：python main.py --api
    # 3. API自定义端口8080：python main.py --api --port=8080
    args_list = sys.argv
    if len(args_list) >= 2 and args_list[1] == "--api":
        target_port = 8000
        # 解析端口参数
        for arg in args_list[2:]:
            if arg.startswith("--port="):
                target_port = int(arg.split("=")[1])
        # 修复：传参用 api_port=
        run_fastapi_server(api_port=target_port)
    else:
        run_console_interactive()