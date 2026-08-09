import requests
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)  # "demo.mcp.mcp_client"

_LOCAL_MCP_PROXY_URL = "http://localhost:8002"

def get_mcp_tool_list() -> list:
    """
    直接HTTP调用MCP代理，获取所有远程工具
    """

    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream"
    }

    try:
        resp = requests.get(
            url=_LOCAL_MCP_PROXY_URL + "/api/tools",
            headers=headers,
            timeout=15
        )
        resp.raise_for_status()

        # MCP SSE 返回会有多行data:xxx，只提取第一条合法JSON
        # raw_lines = .strip().splitlines()
        
        tool_list = json.loads(resp.text)["tools"]

        return tool_list if tool_list is not None else []

    except Exception as e:
        print("请求MCP代理失败：", str(e))
        return []

def call_mcp_tool(tool_name: str, **kwargs) -> str:
    """
    直接HTTP调用MCP代理，执行远程工具
    """
    headers = {
        "Content-Type": "application/json"
    }

    try:
        payload = {
            "tool_name": tool_name,
            "arguments": kwargs
        }
        logging.info(f"发起MCP调用，payload: {payload}")

        resp = requests.post(
            url=_LOCAL_MCP_PROXY_URL + "/api/call",
            headers=headers,
            json=payload,
            timeout=15
        )
        resp.raise_for_status()  # 抛出4xx/5xx HTTP错误

        resp_json = resp.json()
        logging.info(f"代理原始完整返回: {resp_json}")  # 打印整包看结构

        return extract_mcp_data(resp_json, tool_name)

    except KeyError as e:
        logging.error(f"MCP调用参数缺失：{str(e)}")
        return f"[MCP调用参数缺失: {e}]"
    except Exception as e:
        logging.error(f"请求MCP代理失败：{str(e)}", exc_info=True)
        return f"[请求MCP代理失败: {e}]"


def extract_mcp_data(api_response: dict, tool_name: str = "") -> str:
    """
    解析MCP代理/api/call返回的CallToolResult结构，提取模型可读文本。

    MCP标准返回格式：
        {
            "result": [
                {
                    "type": "text",
                    "text": "...",          # 工具返回内容（JSON字符串或纯文本）
                    "is_error": false
                }
            ]
        }
    错误格式：
        {"error": "错误描述"}

    :param api_response: requests.post 之后 .json() 的完整字典
    :param tool_name: 工具名（日志用）
    :return: 模型可读的文本字符串，直接喂给 LLM 作为 tool result
    """
    # 1. 代理层报错
    if api_response.get("error") is not None:
        logging.error(f"MCP代理异常：{api_response['error']}")
        return f"[MCP代理异常: {api_response['error']}]"

    # 2. 取 CallToolResult
    #    两种格式：
    #    a) {"result": [{content:..., is_error:...}]}  — 标准包裹格式
    #    b) {content:..., is_error:...}                — 代理直接返回 CallToolResult
    result_list = api_response.get("result", [])
    if result_list:
        tool_result = result_list[0]
    elif "content" in api_response:
        tool_result = api_response
    else:
        logging.warning(f"MCP返回结果为空: {tool_name}")
        return "[MCP返回空结果]"

    # 4. 工具执行报错（is_error=True）
    if tool_result.get("is_error", False):
        content = tool_result.get("content", [])
        err_msg = content[0].get("text", "未知错误") if content else "未知错误"
        logging.error(f"工具执行失败：{err_msg}")
        return f"[工具 {tool_name} 执行失败: {err_msg}]"

    # 5. 提取 content 中所有 text 块
    content_blocks = tool_result.get("content", [])
    if not content_blocks:
        return "[MCP返回无content]"

    texts = []
    for block in content_blocks:
        if block.get("type") == "text":
            raw_text = block.get("text", "")

            # 尝试解析JSON，美化后返回（模型更易理解结构化数据）
            try:
                parsed = json.loads(raw_text)
                texts.append(json.dumps(parsed, ensure_ascii=False, indent=2))
            except (json.JSONDecodeError, TypeError):
                # 非JSON，直接作为纯文本
                texts.append(raw_text)

    return "\n".join(texts) if texts else "[MCP返回无文本内容]"

if __name__ == "__main__":
    # print(get_mcp_tool_list())
    print(call_mcp_tool("maps_weather", arguments={"city": "厦门"}))