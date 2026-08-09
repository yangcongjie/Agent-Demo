import subprocess
import atexit
import requests
import time
import os
import logging

logger = logging.getLogger(__name__)  # "demo.mcp.start_mcp"

proxy_process = None
PROXY_URL = "http://127.0.0.1:8002"

# 用 __file__ 定位 mcp_proxy_server.py，避免路径拼接错误
_PROXY_SCRIPT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "mcp_proxy_server.py"
)
_PROXY_EXE = r"C:\Users\Sh11p\anaconda3\envs\mcp-client\python.exe"



def start_mcp_proxy():
    global proxy_process

    if not os.path.exists(_PROXY_SCRIPT):
        logger.error(f"代理脚本不存在: {_PROXY_SCRIPT}")
        return False

    cmd = [
        _PROXY_EXE,
        _PROXY_SCRIPT
    ]
    # 优先用当前 Python 直接启动（避免 conda 不在 PATH 的问题）
    # 如需指定 conda 环境，改为: ["conda", "run", "-n", "mcp-client", "python", _PROXY_SCRIPT]
    logger.info(f"启动 MCP 代理: {' '.join(cmd)}")

    proxy_process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )

    # 等待代理启动完成
    for i in range(10):
        try:
            requests.get(f"{PROXY_URL}/api/tools", timeout=1)
            logger.info("MCP 代理启动成功")
            return True
        except Exception:
            # 检查子进程是否已崩溃
            if proxy_process.poll() is not None:
                # 读取错误输出
                output = (
                    proxy_process.stdout.read().decode("utf-8", errors="replace")
                    if proxy_process.stdout
                    else ""
                )
                logger.error(
                    f"MCP 代理进程已退出 (code={proxy_process.returncode})\n{output[:500]}"
                )
                return False
            time.sleep(0.5)

    logger.error("MCP 代理启动超时（5秒内未响应）")
    return False


def stop_mcp_proxy():
    if proxy_process:
        proxy_process.terminate()
        proxy_process.wait()
        logger.info("MCP 代理已关闭")


# 程序退出自动关闭代理
atexit.register(stop_mcp_proxy)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    success = start_mcp_proxy()
    if success:
        print(f"MCP 代理运行中: {PROXY_URL}")
    else:
        print("MCP 代理启动失败，请检查日志")
