import logging

from demo.logger_config import setup_logging
from demo.agent_runtime import AgentRuntime

logger = logging.getLogger(__name__)  # "demo.main"

if __name__ == "__main__":
    setup_logging()
    logger.info("日志配置已加载")

    agent = AgentRuntime()
    logger.info("Agent 已启动")

    print("请输入问题或 'quit' 退出\n")
    while True:
        # 交互式测试
        user_input = input("用户: ").strip()
        # 退出循环
        if user_input.lower() in ("quit", "exit", "q"):
            logger.info("Agent 退出")
            break
        if not user_input:
            continue

        logger.info(f"用户输入: {user_input}")
        answer = agent.run(user_input)
        print(f"智能助手: {answer}\n")

        # 打印本次 trace
        if agent.trace:
            print("--- Trace ---")
            for t in agent.trace:
                print(f"  {t['tool']}({t['args']}) → {t.get('result') or t.get('error')}")
            print()
