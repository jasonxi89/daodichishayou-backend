import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

APP_VERSION = "1.16.0"

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'food_trends.db'}")
API_PORT = int(os.getenv("API_PORT", "8900"))
CRAWL_INTERVAL_HOURS = int(os.getenv("CRAWL_INTERVAL_HOURS", "6"))
# 智能调度：cron 模式在饭点前加密采集 (CST 时区)
# 格式: "HH:MM,HH:MM,..." — 默认 7:00, 10:30, 16:30, 22:00
CRAWL_SCHEDULE_HOURS = os.getenv(
    "CRAWL_SCHEDULE_HOURS", "7:00,10:30,16:30,22:00"
)
CRAWL_USE_SMART_SCHEDULE = os.getenv(
    "CRAWL_USE_SMART_SCHEDULE", "true"
).lower() == "true"
ALLOWED_CLAUDE_EFFORTS = ("low", "medium", "high", "xhigh", "max")


def read_claude_effort() -> str:
    """读取思考强度；填错就在启动时报错，免得每次调用才收到接口的 400。"""
    effort = os.getenv("CLAUDE_EFFORT", "low")
    if effort not in ALLOWED_CLAUDE_EFFORTS:
        raise RuntimeError(
            f"CLAUDE_EFFORT={effort!r} 无效，可选：{', '.join(ALLOWED_CLAUDE_EFFORTS)}"
        )
    return effort


ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")
# 主模型失败后换它重试一次；留空即关闭
CLAUDE_FAST_MODEL = os.getenv("CLAUDE_FAST_MODEL", "")
# 本项目默认 low：预生成和爬虫每天几十到上百次调用，实时接口还要在小程序的超时内返回
CLAUDE_EFFORT = read_claude_effort()
# 两个用途：相邻数据之间的读超时（所有调用），以及实时接口单次调用的总时限
LLM_TIMEOUT_SECONDS = int(os.getenv("LLM_TIMEOUT_SECONDS", "60"))
PREGEN_ENABLED = os.getenv("PREGEN_ENABLED", "true").lower() == "true"
PREGEN_DAILY_BUDGET = int(os.getenv("PREGEN_DAILY_BUDGET", "120"))
AI_EXTRACT_ENABLED = os.getenv("AI_EXTRACT_ENABLED", "true").lower() == "true"
RECIPE_SCRAPE_ENABLED = os.getenv("RECIPE_SCRAPE_ENABLED", "false").lower() == "true"
RECIPE_SCRAPE_INTERVAL_DAYS = int(os.getenv("RECIPE_SCRAPE_INTERVAL_DAYS", "7"))

# 所有 AI 端点共享的核心规则，必须嵌入每个 system prompt
AI_CORE_RULES = """【核心规则 - 所有回答必须遵守】
1. 只能返回真实存在的、广为人知的食物/菜品名称，绝对不能编造或杜撰任何不存在的食物
2. 如果不确定某个菜品是否真实存在，宁可不推荐也不要瞎编
3. 必须严格按照指定的JSON格式返回，不要添加任何其他文字"""
