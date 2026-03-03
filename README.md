# "What to Eat" — Food Trending API Backend

Backend service that powers the WeChat mini program "到底吃啥哟" (What to Eat), providing real-time food trending rankings and AI-powered recipe recommendations.

## Features

- **Trending Rankings API** — aggregates popular food data from multiple sources
- **AI Content Extraction** — uses Claude API to extract food keywords from unstructured web content (150+ food dictionary)
- **Auto Crawling** — scheduled crawler runs every 6 hours via APScheduler
- **Recipe Scraping** — collects recipes with configurable intervals

## Tech Stack

- **FastAPI** — async web framework
- **SQLAlchemy** — ORM
- **SQLite** — database
- **APScheduler** — scheduled crawling
- **httpx** — async HTTP client
- **Claude API** — AI-powered content extraction
- **Docker** — containerized deployment
- **GitHub Actions** — CI/CD

## Quick Start

### Local

```bash
pip install -r requirements.txt
uvicorn app.main:app --port 8900
```

Visit http://localhost:8900/docs for API documentation.

### Docker

```bash
docker compose up --build
```

## API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/trending` | Trending rankings (supports limit/offset/source/category filters) |
| GET | `/api/trending/categories` | Category list |
| GET | `/api/trending/sources` | Data source list |
| POST | `/api/trending/crawl` | Manually trigger crawler |
| POST | `/api/trending/import` | Manually import data |
| GET | `/api/health` | Health check |

## Data Sources

| Source | Description |
|--------|-------------|
| manual | Built-in seed data (20 popular foods) |
| toutiao | Toutiao (Today's Headlines) hot search — filters food-related topics in real-time |
| baidu_suggest | Baidu search suggestions — discovers trending foods via autocomplete |

Crawler runs automatically every 6 hours. Seed data is imported on first startup.

## Project Structure

```
app/
├── main.py              # FastAPI entry point
├── config.py            # Configuration
├── database.py          # Database connection
├── models.py            # ORM models
├── schemas.py           # Request/response schemas
├── routers/
│   └── trending.py      # API routes
└── crawler/
    ├── base.py          # Crawler base class
    ├── food_keywords.py # Food keyword dictionary (150+ foods)
    ├── toutiao.py       # Toutiao hot search crawler
    ├── baidu_suggest.py # Baidu suggestions crawler
    └── scheduler.py     # Scheduled task manager
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `sqlite:///data/food_trends.db` | Database connection |
| `API_PORT` | `8900` | Server port |
| `CRAWL_INTERVAL_HOURS` | `6` | Crawler interval (hours) |

---

# 到底吃啥哟 — 美食热度API后端

为微信小程序「到底吃啥哟」提供美食热度排行数据的后端服务。

## 功能

- **热度排行 API** — 从多个数据源聚合热门美食数据
- **AI 内容提取** — 使用 Claude API 从非结构化网页内容中提取美食关键词（150+ 食物词典）
- **自动爬虫** — APScheduler 定时每6小时执行一次
- **菜谱抓取** — 可配置间隔的菜谱收集

## 技术栈

- **FastAPI** - Web框架
- **SQLAlchemy** - ORM
- **SQLite** - 数据库
- **APScheduler** - 定时爬虫调度
- **httpx** - HTTP客户端
- **Claude API** - AI内容提取
- **Docker** - 容器化部署
- **GitHub Actions** - CI/CD

## 快速开始

### 本地运行

```bash
pip install -r requirements.txt
uvicorn app.main:app --port 8900
```

访问 http://localhost:8900/docs 查看API文档。

### Docker部署

```bash
docker compose up --build
```

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/trending` | 热度排行（支持 limit/offset/source/category 筛选） |
| GET | `/api/trending/categories` | 分类列表 |
| GET | `/api/trending/sources` | 数据来源列表 |
| POST | `/api/trending/crawl` | 手动触发爬虫 |
| POST | `/api/trending/import` | 手动导入数据 |
| GET | `/api/health` | 健康检查 |

## 数据来源

| 来源 | 说明 |
|------|------|
| manual | 内置种子数据（20种热门食物） |
| toutiao | 今日头条热搜（实时筛选美食相关话题） |
| baidu_suggest | 百度搜索建议（通过联想词发现热门食物） |

爬虫每6小时自动运行一次，首次启动自动导入种子数据。

## 项目结构

```
app/
├── main.py              # FastAPI入口
├── config.py            # 配置
├── database.py          # 数据库连接
├── models.py            # ORM模型
├── schemas.py           # 请求/响应模型
├── routers/
│   └── trending.py      # API路由
└── crawler/
    ├── base.py          # 爬虫基类
    ├── food_keywords.py # 美食关键词词典（150+食物）
    ├── toutiao.py       # 头条热搜爬虫
    ├── baidu_suggest.py # 百度建议爬虫
    └── scheduler.py     # 定时调度
```

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DATABASE_URL` | `sqlite:///data/food_trends.db` | 数据库连接 |
| `API_PORT` | `8900` | 服务端口 |
| `CRAWL_INTERVAL_HOURS` | `6` | 爬虫执行间隔（小时） |
