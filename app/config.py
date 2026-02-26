import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'food_trends.db'}")
API_PORT = int(os.getenv("API_PORT", "8900"))
CRAWL_INTERVAL_HOURS = int(os.getenv("CRAWL_INTERVAL_HOURS", "6"))
CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY", "")

# 所有 AI 端点共享的核心规则，必须嵌入每个 system prompt
AI_CORE_RULES = """【核心规则 - 所有回答必须遵守】
1. 只能返回真实存在的、广为人知的食物/菜品名称，绝对不能编造或杜撰任何不存在的食物
2. 如果不确定某个菜品是否真实存在，宁可不推荐也不要瞎编
3. 必须严格按照指定的JSON格式返回，不要添加任何其他文字"""
