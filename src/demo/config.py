"""
全局配置中心
- 基于 pydantic-settings，支持从 .env 文件读取敏感配置
- 使用方式：from demo.config import Config
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path


BASE_DIR = Path(__file__).parent.parent.parent  # src/demo → src → 项目根目录

class Config(BaseSettings):
    BASE_DIR: Path = BASE_DIR
    """全局配置，优先从 .env 读取，未设置则用默认值"""

    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    # ========== LLM大模型配置 ==========
    LLM_API_KEY: str
    LLM_BASE_URL: str = (
        "https://ws-rhkqzmk4d8v01jz0.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
    )
    LLM_MODEL: str = "qwen-plus"
    LLM_TEMPERATURE: float = 0.1

    # ========== Agent Runtime 配置 ==========
    AGENT_MAX_LOOP: int = 10  # Agent最大工具调用轮次，防止死循环

    # ========== Context上下文管理配置 ==========
    CONTEXT_MAX_KEEP_MESSAGES: int = 15  # 保留最近多少条完整消息，超过触发摘要压缩

    # ========== 调试打印开关 ==========
    DEBUG_PRINT_LLM_RAW: bool = True  # 是否打印LLM原始输出


config = Config()
