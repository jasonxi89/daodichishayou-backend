# 到底吃啥哟 - 美食热度API后端

为微信小程序「到底吃啥哟」提供美食热度排行数据的后端服务。

## 技术栈

- **FastAPI** - Web框架
- **SQLAlchemy** - ORM
- **SQLite** - 数据库
- **APScheduler** - 定时爬虫调度
- **httpx** - HTTP客户端
- **Docker** - 容器化部署

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

### 示例

```bash
# 获取热度Top10
curl http://localhost:8900/api/trending?limit=10

# 按分类筛选
curl http://localhost:8900/api/trending?category=小吃

# 手动触发爬虫
curl -X POST http://localhost:8900/api/trending/crawl
```

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
