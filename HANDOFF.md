# 到底吃啥哟 - 后端交接文档

> 版本 1.8.0 | 2026-04-03 | Python 3.13 + FastAPI + SQLite

## 一、项目概览

微信小程序"到底吃啥哟"的后端 API，核心功能：
1. **美食热度聚合** — 从头条/百度/微博/抖音/B站/知乎/澎湃 7 个平台爬取热搜，匹配食物名
2. **AI 智能补充** — 词典未匹配的标题交给 Claude 提取新食物
3. **AI 趋势快报** — 每次采集后自动生成"今日美食趋势"摘要
4. **AI 食材推荐** — 用户输入食材，AI 推荐菜品做法
5. **菜谱数据库** — 下厨房爬取的本地菜谱，食材推荐时优先使用
6. **历史趋势** — 每日快照，支持查询食物热度变化曲线

---

## 二、快速启动

```bash
# 本地运行
cd C:\Users\goodb\daodichishayou-backend
pip install -r requirements.txt
CLAUDE_API_KEY=sk-xxx uvicorn app.main:app --port 8900

# Docker
docker compose up --build

# 测试
pytest tests/ -v
```

API 文档: http://localhost:8900/docs

---

## 三、目录结构

```
app/
├── main.py              # FastAPI 入口，lifespan 中注册调度器
├── config.py            # 所有配置项 + AI 共享规则
├── database.py          # SQLAlchemy engine/session
├── models.py            # 8 个 ORM 模型
├── schemas.py           # Pydantic 请求/响应模型
├── routers/
│   ├── trending.py      # 热度排行 / 趋势快报 / 历史查询
│   ├── recommend.py     # AI 食材推荐 / 分类食物生成
│   └── recipe.py        # 菜谱搜索 / 浏览
└── crawler/
    ├── base.py          # BaseCrawler 抽象类
    ├── food_keywords.py # 500+ 食物词典 (15 分类)
    ├── toutiao.py       # 头条热搜
    ├── baidu_suggest.py # 百度搜索建议
    ├── dailyhot.py      # DailyHot 聚合 (6 平台)
    ├── ai_extractor.py  # AI 食物提取 (带哈希缓存)
    ├── ai_digest.py     # AI 趋势快报
    ├── scheduler.py     # 调度编排 (爬虫→AI→快照→快报)
    ├── recipe_base.py   # 菜谱爬虫基类
    └── xiachufang.py    # 下厨房爬虫
```

---

## 四、数据流

```
┌─ 定时触发 (cron 7:00/10:30/16:30/22:00 CST) ──────────────────────────┐
│                                                                          │
│  scheduler.py: run_all_crawlers(db)                                     │
│                                                                          │
│  Step 1: 爬虫采集                                                        │
│  ┌──────────┐  ┌──────────────┐  ┌───────────────────────────────┐      │
│  │ 头条热搜  │  │ 百度搜索建议  │  │ DailyHot (微博/抖音/B站/      │      │
│  │ toutiao   │  │ baidu_suggest│  │ 百度/知乎/澎湃)               │      │
│  └────┬─────┘  └──────┬───────┘  └──────────────┬────────────────┘      │
│       │               │                          │                       │
│       └───────────────┼──────────────────────────┘                       │
│                       ▼                                                  │
│  food_keywords.py: 词典匹配 (500+ 食物名)                                │
│       │                                                                  │
│       ├─ 匹配成功 → food_trends 表 (upsert by food_name+source)         │
│       └─ 未匹配 → all_unmatched 列表                                     │
│                                                                          │
│  Step 2: AI 智能提取                                                     │
│  ai_extractor.py:                                                        │
│       │                                                                  │
│       ├─ 标题去重 → SHA256 哈希                                          │
│       ├─ 查 ai_title_cache 表 → 命中则直接取结果                         │
│       └─ 未命中 → Claude API → 解析 → 写缓存 + 写 food_trends           │
│                                                                          │
│  Step 3: 保存快照                                                        │
│  _save_daily_snapshot(db):                                               │
│       └─ food_trends 全量 → food_trend_snapshots (当天 upsert)          │
│                                                                          │
│  Step 4: 生成快报                                                        │
│  ai_digest.py: generate_daily_digest(db)                                │
│       └─ Top 30 热度数据 → Claude API → food_digests (当天 upsert)      │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 五、数据库模型

### 核心表

| 表 | 文件 | 唯一约束 | 说明 |
|----|------|----------|------|
| `food_trends` | models.py:13 | (food_name, source) | 美食热度主表，所有爬虫写入 |
| `food_trend_snapshots` | models.py:102 | (snapshot_date, food_name, source) | 每日快照，用于历史趋势 |
| `food_digests` | models.py:85 | digest_date | 每日 AI 趋势快报 |

### 辅助表

| 表 | 说明 |
|----|------|
| `crawl_logs` | 爬虫执行日志 (source + status + error_message) |
| `ai_discovered_foods` | AI 发现的新食物 (food_name unique + discovery_count) |
| `ai_title_cache` | 标题提取缓存 (title_hash SHA256 → extracted_foods JSON) |
| `recipes` | 下厨房菜谱 (source_url unique, 含评分/做过数/食材/步骤) |
| `foods_category_cache` | AI 分类食物缓存 (category unique, 1天过期) |

### 关系概览

```
food_trends ──(快照)──→ food_trend_snapshots
    │                        │
    │                        └─ 历史查询: GET /api/trending/history/{name}
    │
    └──(Top 30)──→ AI ──→ food_digests
                            │
                            └─ 快报查询: GET /api/trending/digest
```

---

## 六、API 端点速查

### 热度相关
```
GET  /api/trending                          # 热度排行 (limit/offset/source/category)
GET  /api/trending/categories               # 分类列表
GET  /api/trending/sources                  # 来源列表
GET  /api/trending/digest?date=2026-04-03   # 今日趋势快报
GET  /api/trending/history/火锅?days=7      # 火锅最近7天热度
POST /api/trending/crawl                    # 手动触发一轮爬虫
POST /api/trending/import                   # 手动导入热度数据
```

### AI 推荐
```
POST /api/recommend                         # 食材→菜品 (本地菜谱优先, 不够调AI)
POST /api/foods-by-category                 # 按分类生成食物列表
POST /api/bulk-foods-by-category            # 批量分类食物 (一次AI调用)
```

### 菜谱
```
GET  /api/recipes/search?ingredients=鸡蛋,番茄  # 食材匹配搜索
GET  /api/recipes?limit=20&category=honor       # 浏览筛选
POST /api/recipes/scrape                         # 手动触发爬取
```

### 系统
```
GET  /api/health    # {"status":"ok","version":"1.8.0"}
```

---

## 七、环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `CLAUDE_API_KEY` | (必填) | Anthropic API 密钥 |
| `CLAUDE_MODEL` | `claude-opus-4-6` | 统一 LLM 模型名 |
| `AI_EXTRACT_ENABLED` | `true` | AI 食物提取开关 |
| `CRAWL_USE_SMART_SCHEDULE` | `true` | 智能调度开关 |
| `CRAWL_SCHEDULE_HOURS` | `7:00,10:30,16:30,22:00` | 智能调度时间点 (CST) |
| `CRAWL_INTERVAL_HOURS` | `6` | 传统模式爬虫间隔 (智能调度关闭时生效) |
| `RECIPE_SCRAPE_INTERVAL_DAYS` | `7` | 菜谱爬取间隔 |
| `DAILYHOT_API_URL` | `http://dailyhot-api:6688` | DailyHot 聚合 API |
| `DATABASE_URL` | `sqlite:///data/food_trends.db` | 数据库连接串 |
| `API_PORT` | `8900` | 服务端口 |
| `TZ` | `Asia/Shanghai` | 时区 |

---

## 八、调度机制

### 智能调度 (默认, v1.8.0)

APScheduler cron 模式，按 `CRAWL_SCHEDULE_HOURS` 配置的时间点触发：

| 时间 (CST) | 意义 |
|------------|------|
| 07:00 | 早间采集 — 抓取前一晚积累的热搜 |
| 10:30 | 午饭前采集 — 为中午"吃啥"决策提供最新数据 |
| 16:30 | 晚饭前采集 — 为晚餐决策服务 |
| 22:00 | 晚间采集 — 捕捉夜宵/次日趋势 |

每次触发执行完整流水线：爬虫 → AI 提取 → 快照 → 快报。

### 传统模式 (回退)

设置 `CRAWL_USE_SMART_SCHEDULE=false`，回退到固定 N 小时间隔。

### 菜谱爬取

独立调度，默认每 7 天执行一次（`RECIPE_SCRAPE_INTERVAL_DAYS`）。

---

## 九、AI 集成详解

### 统一配置
- 模型: `config.py` → `CLAUDE_MODEL` (默认 `claude-opus-4-6`)
- 所有 AI 端点共享 `AI_CORE_RULES`，核心约束：只返回真实食物、JSON 格式

### 功能矩阵

| 功能 | 文件 | 触发方式 | 输入 | 输出 |
|------|------|----------|------|------|
| 食物提取 | `ai_extractor.py` | 每次爬虫后自动 | 未匹配标题 | 新食物名+分类 |
| 趋势快报 | `ai_digest.py` | 每次爬虫后自动 | Top 30 热度数据 | 摘要+Top食物+推荐 |
| 食材推荐 | `recommend.py` | POST /api/recommend | 食材列表 | 菜品+做法 |
| 分类食物 | `recommend.py` | POST /api/foods-by-category | 分类名 | 食物列表 |

### 哈希缓存机制 (ai_extractor.py)

```
输入标题 → SHA256 → 查 ai_title_cache
  ├─ 命中 → 返回缓存的 extracted_foods
  └─ 未命中 → 调 Claude → 解析 → 写缓存 → 返回
```

缓存永不过期（同一标题的食物提取结果不变）。如需清理：
```sql
DELETE FROM ai_title_cache;
```

---

## 十、常见调试场景

### 爬虫不工作
```sql
-- 查看最近爬虫日志
SELECT * FROM crawl_logs ORDER BY created_at DESC LIMIT 20;
```
- `status=failed` → 看 `error_message`
- DailyHot 失败 → 检查 `dailyhot-api` 容器是否运行
- 头条/百度 失败 → 可能被限流，看 HTTP 状态码

### AI 提取没结果
```bash
# 看日志中的缓存命中率
grep "标题缓存" app.log
# 示例: "标题缓存: 45 命中, 5 需调用 AI"
```
- 全部命中 = 没有新标题，正常
- `CLAUDE_API_KEY` 未配置 → 日志会有 warning
- `AI_EXTRACT_ENABLED=false` → 所有 AI 提取被禁用

### 趋势快报为空
```sql
SELECT * FROM food_digests ORDER BY digest_date DESC LIMIT 5;
```
- `food_trends` 表为空 → 没有数据，AI 无法生成
- Claude API 错误 → 看日志 `AI 趋势总结调用失败`
- JSON 解析失败 → 看日志 `AI 趋势总结 JSON 解析失败`

### 历史数据不准
```sql
-- 查看某食物的快照
SELECT * FROM food_trend_snapshots
WHERE food_name = '火锅'
ORDER BY snapshot_date DESC;
```
快照是每次爬虫完成后全量 `food_trends` 的副本，同一天多次爬虫只保留最后一次。

### 调度时间不对
```bash
# 确认容器时区
docker exec <container> date
# 应该显示 CST (Asia/Shanghai)
```
- `TZ=Asia/Shanghai` 必须在 docker-compose 中设置
- APScheduler 的 cron job 使用 `timezone="Asia/Shanghai"`

### 手动触发一轮采集
```bash
curl -X POST http://localhost:8900/api/trending/crawl
```
会执行完整流水线（爬虫→AI提取→快照→快报）。

---

## 十一、部署流程

```bash
# 1. 改代码 → push → CI 自动构建 Docker 镜像
git push

# 2. 获取 commit SHA
git rev-parse HEAD
# 输出: abc123...

# 3. 更新 NAS compose 文件 image tag
# 位置: /zspace/applications/services/zdocker/config/compose_config/daodichishayou-backend.yaml
# ⚠️ 必须用 SHA tag，不用 :latest（NAS mirror 缓存问题）
# image: jasonxi89/daodichishayou-backend:abc123...

# 4. NAS 部署 (通过 nas_ssh.py)
python ~/nas_ssh.py  # 按提示操作

# 5. 验证
curl https://food.zuitian.ai/api/health
# {"status":"ok","version":"1.8.0"}
```

---

## 十二、测试

```bash
# 全量测试
pytest tests/ -v

# 单文件
pytest tests/crawler/test_ai_extractor.py -v

# 覆盖率
pytest tests/ --cov=app --cov-report=term-missing
```

当前 203 tests，覆盖率 ~98%。CI 门控阈值 95%。

### 测试要点
- AI 相关测试 mock `Anthropic` 客户端和 `SessionLocal`
- `_parse_response` 返回 `(items, title_mapping)` 元组
- 爬虫测试 mock HTTP 请求 (`requests.get`)
- Schema 测试验证默认值和字段类型

---

## 十三、v1.8.0 变更清单

| 改动 | 文件 | 说明 |
|------|------|------|
| 统一 LLM | config.py | 新增 `CLAUDE_MODEL`，默认 `claude-opus-4-6` |
| | ai_extractor.py | haiku → `CLAUDE_MODEL` |
| | recommend.py | sonnet → `CLAUDE_MODEL` (3处) |
| AI 趋势快报 | crawler/ai_digest.py | 新建，爬虫后生成趋势摘要 |
| | models.py | 新增 `FoodDigest` 模型 |
| | schemas.py | 新增 `FoodDigestOut` |
| | routers/trending.py | 新增 `GET /digest` 端点 |
| 智能调度 | config.py | 新增 `CRAWL_USE_SMART_SCHEDULE` + `CRAWL_SCHEDULE_HOURS` |
| | main.py | lifespan 改为 cron/interval 双模式 |
| 哈希缓存 | crawler/ai_extractor.py | 重写，加 SHA256 缓存层 |
| | models.py | 新增 `AITitleCache` 模型 |
| 历史快照 | models.py | 新增 `FoodTrendSnapshot` 模型 |
| | crawler/scheduler.py | 新增 `_save_daily_snapshot()` |
| | routers/trending.py | 新增 `GET /history/{food_name}` 端点 |
| | schemas.py | 新增 `FoodTrendSnapshotOut` + `TrendHistoryResponse` |
| 版本号 | config.py | 1.7.2 → 1.8.0 |
