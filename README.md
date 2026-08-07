# 到底吃啥哟 — 后端

微信小程序「到底吃啥哟」的后端服务，提供多源美食热度排行、食材配菜推荐、菜谱搜索和每日趋势快报。

**当前应用版本 / 线上生产版本：v1.15.1**

## 核心能力

- 聚合今日头条、DailyHotApi、百度搜索建议等来源的美食热度，并按别名规范名聚合展示。
- 使用 15 个分类、502 个去重食物名的本地词典筛选热搜；未匹配标题可交给 LLM 提取并缓存。
- 根据现有食材生成完整菜谱，或先返回轻量菜名卡片、再按需生成做法步骤。
- 标准推荐链路（无偏好且不允许额外食材）支持七天缓存、本地完整菜谱优先和 LLM 不可用时的本地降级。
- 搜索和浏览本地菜谱库；菜谱抓取功能可配置，代码默认关闭。
- 每轮热度抓取后保存每日快照，并在 LLM 可用时生成每日趋势快报。
- 为美食分类生成俏皮小注并永久缓存；生成失败时以 `null` 静默降级。
- 通过人工管理端点执行热度导入、爬虫触发、菜谱抓取和食物别名归并。

## 技术栈

| 组件 | 仓库版本 / 配置 |
|------|-----------------|
| Python | 3.11（Docker 与 CI） |
| FastAPI | 0.115.6 |
| Uvicorn | 0.34.0 |
| SQLAlchemy | 2.0.36 |
| Pydantic | 2.10.4 |
| SQLite | WAL 模式，`synchronous=NORMAL`，连接超时 30 秒 |
| APScheduler | 3.10.4 |
| httpx | 0.28.1 |
| BeautifulSoup4 | >= 4.12 |
| openai SDK | 2.42.0，调用 OpenAI 兼容接口 |
| 测试 | pytest >= 8.0、pytest-asyncio >= 0.23、pytest-cov >= 4.1 |
| 交付 | Docker、GitHub Actions |

## 快速开始

以下命令均在仓库根目录执行。macOS 本地虚拟环境使用 `.venv/bin/python`。

### 本地运行

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8900
```

服务无需 LLM 凭据也能启动；此时 AI 提取和趋势快报会跳过，依赖 LLM 且没有缓存或本地降级结果的请求会返回相应错误。

首次连接空数据库时，服务会自动建表、执行幂等迁移并导入 20 条 `manual` 种子数据。默认数据库文件位于仓库的 `data/food_trends.db`。

### Docker

```bash
docker compose up --build
docker compose down
```

仓库内 Compose 同时启动 API 和 DailyHotApi，并通过 `./data:/app/data` 持久化 SQLite 数据。不要删除该数据卷。

### 测试

```bash
.venv/bin/python -m pytest
.venv/bin/python -m pytest --cov=app --cov-report=term-missing --cov-fail-under=95
```

`pytest.ini` 将 `testpaths` 设为 `tests`，并启用 `asyncio_mode=auto`。CI 使用 Python 3.11 执行带覆盖率的命令，覆盖率低于 95% 时停止后续镜像构建。

## LLM 渠道

所有 LLM 功能都通过 `openai` SDK 的 `chat.completions` 接口调用，渠道、模型和超时完全由环境变量决定。环境变量仍沿用 `OPENROUTER_*` 历史命名，但并不限定只能使用 OpenRouter。

- **代码默认配置**：`OPENROUTER_BASE_URL=https://openrouter.ai/api/v1`，`OPENROUTER_MODEL=deepseek/deepseek-v4-pro`。
- **线上生产配置（自 2026-07-17 起）**：DeepSeek 官网直连，`OPENROUTER_BASE_URL=https://api.deepseek.com`，`OPENROUTER_MODEL=deepseek-v4-pro`。官网模型名没有 `deepseek/` 前缀。
- 仓库内 `docker-compose.yml` 保持 OpenRouter 兼容默认值；生产环境由 NAS Compose 注入直连配置。切换模型或渠道只需修改 NAS Compose 环境变量并重新创建容器，不需要改代码。
- LLM API 凭据只保存在未纳入版本控制的本地环境或 NAS Compose 环境变量中；不要写入源码或文档。

## 环境变量

下表默认值来自 `app/config.py`；`DAILYHOT_API_URL` 来自对应爬虫模块。

| 变量 | 代码默认值 | 说明 |
|------|------------|------|
| `DATABASE_URL` | 由 `data/food_trends.db` 的绝对路径生成 | SQLAlchemy 数据库 URL |
| `API_PORT` | `8900` | 应用配置值；实际监听端口由 Uvicorn / Docker 启动参数决定 |
| `CRAWL_USE_SMART_SCHEDULE` | `true` | 启用固定时刻的智能抓取调度 |
| `CRAWL_SCHEDULE_HOURS` | `7:00,10:30,16:30,22:00` | 智能调度时刻，时区为 `Asia/Shanghai` |
| `CRAWL_INTERVAL_HOURS` | `6` | 关闭智能调度后使用的固定间隔小时数 |
| `OPENROUTER_API_KEY` | 空 | 当前 LLM 渠道的凭据 |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | OpenAI 兼容 API 地址 |
| `OPENROUTER_MODEL` | `deepseek/deepseek-v4-pro` | 主模型；DeepSeek 官网直连时使用无供应商前缀的模型名 |
| `OPENROUTER_FAST_MODEL` | 空 | 推荐主模型失败后的可选串行重试模型；空值表示关闭 |
| `LLM_TIMEOUT_SECONDS` | `60` | 所有 LLM 请求超时秒数 |
| `AI_EXTRACT_ENABLED` | `true` | 是否对词典未匹配的标题执行 AI 提取 |
| `PREGEN_ENABLED` | `true` | 是否启用推荐结果预生成 |
| `PREGEN_DAILY_BUDGET` | `120` | 每次预生成最多尝试的 LLM 调用数 |
| `RECIPE_SCRAPE_ENABLED` | `false` | 是否启用菜谱定时抓取和手动抓取端点 |
| `RECIPE_SCRAPE_INTERVAL_DAYS` | `7` | 菜谱抓取间隔天数 |
| `DAILYHOT_API_URL` | `http://localhost:6688` | DailyHotApi 地址；仓库 Compose 覆盖为容器内服务地址 |

## API

当前代码共注册 18 个业务 API 端点。

### 服务状态

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 返回服务状态与 `APP_VERSION` |

### 热度与趋势

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/trending` | 热度排行；支持 `limit`、`offset`、`source`、`category`、`aggregate`，默认按规范名聚合 |
| GET | `/api/trending/categories` | 返回已有分类名列表 |
| GET | `/api/trending/categories/annotated` | 返回分类名和小注；缺失小注时批量生成并缓存，失败或无 LLM 凭据时 `note` 为 `null` |
| GET | `/api/trending/sources` | 返回数据库中已有热度来源列表 |
| POST | `/api/trending/crawl` | 手动执行全部热度爬虫、快照与趋势快报流程 |
| POST | `/api/trending/import` | 批量导入或更新热度记录 |
| GET | `/api/trending/digest` | 获取指定 `date` 的趋势快报；未传日期时返回最新一条，没有数据时返回 `null` |
| GET | `/api/trending/history/{food_name}` | 获取指定食物最近 `days` 天的热度快照，范围 1–90 天 |

分类小注端点的响应形状为：

```json
{
  "categories": [
    {"name": "家常下饭", "note": "妈妈味道"},
    {"name": "其他分类", "note": null}
  ]
}
```

### 食材推荐

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/recommend` | 根据食材返回包含配料和步骤的完整推荐，最多 5 道 |
| POST | `/api/recommend/quick` | 快速返回菜名、简介、难度和耗时，不生成详细步骤 |
| POST | `/api/recommend/steps` | 为选定菜品补全配料与步骤；查询参数 `stream=true` 时返回 NDJSON 流 |
| POST | `/api/foods-by-category` | 为单个分类生成食物名列表，并缓存一天 |
| POST | `/api/bulk-foods-by-category` | 批量为多个分类生成食物名列表，并复用分类缓存 |

步骤流使用 `application/x-ndjson`，可能依次返回 `delta`、`complete` 或 `error` 帧；命中完整缓存时直接返回一条 `complete` 帧。

### 菜谱

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/recipes/search` | 按 `name` 模糊搜索，或按逗号分隔的 `ingredients` 搜索；两者至少提供一个 |
| GET | `/api/recipes` | 分页浏览菜谱，支持 `category` 和 `min_rating` 筛选 |
| POST | `/api/recipes/scrape` | 手动执行下厨房菜谱抓取；代码默认关闭，未启用时返回 403 |

### 管理

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/admin/merge-aliases` | 用 LLM 批量归并食物别名并更新规范名；需要 LLM 凭据 |

## 数据来源与调度

### 热度来源

| 来源值 | 行为 |
|--------|------|
| `manual` | 空数据库首次启动时写入的 20 条种子数据 |
| `toutiao` | 从今日头条热榜筛选命中本地食物词典的标题 |
| `dailyhot` | 从 DailyHotApi 聚合的微博、抖音、B 站、百度、知乎、澎湃热榜中筛选美食 |
| `baidu_suggest` | 从百度搜索联想词产生候选；只有得到其他来源佐证后才晋级热度主表 |
| `ai_extract` | 从常规词典未匹配的标题中提取新食物，并缓存标题处理结果 |

下厨房是独立的菜谱数据来源，不属于热度排行来源。

### 默认调度

- 热度爬虫采用智能 cron 调度，在每天 07:00、10:30、16:30、22:00（`Asia/Shanghai`）运行。
- 关闭智能调度后，热度爬虫改为每 6 小时运行。
- 推荐预生成默认开启，每天 03:30 运行，单次最多尝试 120 个预设食材组合。
- 菜谱抓取代码默认关闭；启用后每 7 天运行一次。

## 项目结构

```text
app/
├── main.py                  # FastAPI 入口、生命周期和 APScheduler 注册
├── config.py                # 版本、环境变量与 AI 核心规则
├── database.py              # SQLAlchemy 引擎、Session、SQLite WAL 配置
├── models.py                # 热度、菜谱、快报、快照、别名与缓存模型
├── schemas.py               # Pydantic 请求/响应模型
├── routers/
│   ├── trending.py          # 热度、分类、快报与历史
│   ├── recommend.py         # 完整推荐与分类食物生成
│   ├── recommend_progressive.py # quick / steps 渐进式推荐
│   ├── recipe.py            # 菜谱搜索、列表与抓取
│   └── admin.py             # 别名归并管理操作
├── crawler/
│   ├── food_keywords.py     # 502 个食物名、15 个分类
│   ├── toutiao.py
│   ├── baidu_suggest.py
│   ├── dailyhot.py
│   ├── ai_extractor.py
│   ├── ai_digest.py
│   ├── xiachufang.py
│   ├── pregen.py
│   └── scheduler.py
├── services/                # 本地菜谱搜索、推荐缓存与降级链
└── migrations/              # 启动时执行的幂等迁移
scripts/                     # 菜谱步骤补全脚本
tests/                       # pytest 测试
```

## CI/CD 与生产约定

- 推送到 `main` 后，GitHub Actions 先执行测试与 95% 覆盖率门控，通过后再构建并推送 Docker 镜像的 `latest` 和 commit SHA 标签。
- 生产部署使用 commit SHA 标签，不使用 `latest`；NAS Compose 更新镜像标签后重新创建容器。
- 必须保留 `./data:/app/data` 挂载，数据库、快照、菜谱和缓存都存放在该目录。
- LLM 凭据保存在 NAS Compose 环境变量；Docker Registry 凭据保存在 GitHub 仓库 Secrets。仓库文档不记录任何具体密钥、token、SSH 凭据或 NAS 密码。
