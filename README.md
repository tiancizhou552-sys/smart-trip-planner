# Smart Trip Planner · 多智能体旅行规划助手

> 输入目的地、日期和偏好，4 个各司其职的 Agent 协作产出一份带景点、天气、酒店、
> 预算和地图的完整行程，导出成 PDF 带走。

**它能做什么**

| 输入                        | 输出                      |
| ------------------------- | ----------------------- |
| 目的地城市、起止日期、天数             | 每日景点安排（含经纬度、建议游览时长、门票价） |
| 交通方式偏好（公共交通 / 自驾 / 步行）    | 早中晚三餐推荐与预估费用            |
| 住宿偏好（经济型 / 舒适型 / 豪华型）     | 每天一个具体酒店推荐（含价格区间、距景点距离） |
| 旅行风格标签（历史文化 / 美食 / 自然风光…） | 逐日天气（白天 / 夜间 / 风向风力）    |
| 自由文本额外要求                  | 预算汇总 + 行程总建议            |

结果页会把景点按经纬度打在高德地图上，支持逐日切换、景点配图，并能把整份行程导出为 PDF。

---

## 目录

- [为什么拆成 4 个 Agent](#为什么拆成-4-个-agent)
- [架构](#架构)
- [工具是怎么被调起来的](#工具是怎么被调起来的)
- [快速开始](#快速开始)
- [目录结构](#目录结构)
- [API](#api)
- [已知限制](#已知限制)

---

## 为什么拆成 4 个 Agent

第一版我试过用一个 Agent 加一个大 prompt 干完所有事，失败了。原因是职责混在一起之后，
模型的注意力被分散：它会在还没拿到真实景点数据的时候就开始编行程，而且 JSON 输出格式
时好时坏，程序经常解析不出来。

拆开之后就稳定了 —— 关键在于把**「取数据」和「组装数据」彻底分离**：

| Agent      | 职责             | 有工具吗     | 关键约束                       |
| ---------- | -------------- | -------- | -------------------------- |
| **景点搜索专家** | 按城市和偏好搜 POI    | ✅ 高德 MCP | prompt 里反复强调"必须用工具，不许编造景点" |
| **天气查询专家** | 查目的地天气         | ✅ 高德 MCP | 同上                         |
| **酒店推荐专家** | 按住宿偏好搜酒店       | ✅ 高德 MCP | 同上                         |
| **行程规划专家** | 把前三者结果组装成结构化行程 | ❌        | prompt 里写死完整 JSON schema   |

前三个 Agent 的 system prompt 只有一件事：**只负责取数、不许自己发挥**。
第四个 Agent 拿不到工具，所以它**没法编造数据**，只能在给定的景点、天气、酒店里面做排布 ——
这一步同时解决了「编造」和「格式不稳」两个问题：它只需要输出 JSON，不需要提问、不需要调用。

这也是我在这个项目里最主要的设计判断：**让不该编造的 Agent 拿不到编造的余地。**

### 兜底与它的代价

行程规划 Agent 的输出会被解析成 `TripPlan`。如果 JSON 解析失败，
`_parse_response` 会调用 `_create_fallback_plan` 生成一份**结构完整的占位行程**
（景点名形如「{城市}景点1」），保证接口不会 500。

好处是服务不会崩；**坏处是用户可能拿到一份看起来正常、实际是占位符的行程**。
所以后端控制台会打印 `⚠️ 解析响应失败`，调试时看到这行就说明走了降级分支。
如果你要把它用到真实场景，这里应该改成把解析失败暴露给用户，而不是静默降级。

---

## 架构

```
前端  Vue 3 + TypeScript + Vite + Ant Design Vue
  ├── Home.vue      旅行需求表单
  └── Result.vue    地图 + 逐日行程 + 预算 + PDF 导出
        │  axios → VITE_API_BASE_URL
        ▼
后端  FastAPI
  ├── POST /api/trip/plan       ★ 主流程（多智能体协作）
  ├── GET  /api/map/poi         地图服务接口
  ├── GET  /api/map/weather
  ├── POST /api/map/route
  └── GET  /api/poi/photo       景点配图（Unsplash）
        │
        ▼
Agent 层  MultiAgentTripPlanner（单例，启动时初始化一次）
  ├── 景点搜索 Agent ─┐
  ├── 天气查询 Agent ─┼─ 共享同一个 MCPTool 实例
  ├── 酒店推荐 Agent ─┘
  └── 行程规划 Agent（无工具，只做组装）
        │
        ▼
工具层  MCP（Model Context Protocol）
  └── amap-mcp-server  →  16 个高德地图工具
        地图服务不是硬编码进代码的，而是通过 MCP 协议挂载外部服务器。
```

**这里最关键的一点：地图能力通过 MCP 挂载，不是写死在 Agent 代码里。**
Agent 启动时连上 `amap-mcp-server`，自动获得 16 个高德工具（POI 搜索、天气、
各类路线规划、地理编码……）。换地图服务商只需要改 `server_command`，Agent 代码一行不用动。

共享一个 MCPTool 实例也是有意的 —— MCP 服务器启动有成本（首次要下载约 24 个包），
4 个 Agent 各建一个既慢又浪费。所以 `MultiAgentTripPlanner.__init__` 里建一次，
再 `add_tool` 给三个需要工具的 Agent。

---

## 工具是怎么被调起来的

这个项目没有用 function calling，而是用了**自定义文本协议**：

```
[TOOL_CALL:amap_maps_text_search:keywords=历史文化,city=北京]
```

`SimpleAgent` 从模型输出里解析这一行 → 找到 `amap_maps_text_search` 工具
（`auto_expand=True` 时 MCP 工具会带 `amap_` 前缀展开成独立工具）→ 真正调 MCP。

所以三个 Agent 的 prompt 里会反复看到"格式必须完全正确，包括方括号和冒号" ——
**格式错了，工具就静默调不起来**，模型会把调用当成普通文本吐出来，然后继续编内容。

我后来发现这个协议不够可靠：模型偶尔会输出变形格式（少个冒号、参数顺序颠倒）。
所以 `_build_attraction_query` 里的做法是**直接构造好那行调用文本拼进 query**，
减少对模型自由发挥的依赖 —— 这是个务实的妥协，用可控性换稳定性。

---

## 快速开始

### 前置条件

- Python 3.10+
- Node.js 16+
- 高德地图 Web 服务 Key（后端 MCP 用）
- 高德地图 Web 端 JS API Key（前端地图渲染用）
- LLM API Key（任意 OpenAI 兼容服务，如 DeepSeek）
- 可选：Unsplash Access Key（景点配图，不配也能跑）

> 高德的这两种 Key **不通用**。MCP 走的是「Web 服务」Key，前端地图走的是「Web 端(JS API)」Key。
> 用错类型会报 `INVALID_USER_KEY` 或 `USERKEY_PLAT_NOMATCH`，地图区域一片空白。

### 后端

```bash
cd backend

python -m venv venv
venv\Scripts\activate          # macOS/Linux: source venv/bin/activate

pip install -r requirements.txt

cp .env.example .env           # 填入 AMAP_API_KEY 与 LLM_API_KEY

python run.py
```

- API 服务：<http://localhost:8000>
- **接口文档：<http://localhost:8000/docs>** ← 建议先用它测 `POST /api/trip/plan`

### 前端

```bash
cd frontend

npm install
cp .env.example .env           # 填入两个高德 Key（Web 服务 + Web端 JS API）

npm run dev
```

浏览器打开 <http://localhost:5173>。

**后端必须先起着**，否则前端页面能打开，但点「生成行程」会请求失败。
前端默认直连 `http://localhost:8000`（见 `VITE_API_BASE_URL`），不走 vite proxy；
后端的 CORS 白名单已包含 `localhost:5173`。

> 关于 `uvx`：`requirements.txt` 里已经包含 `uv>=0.8.0`，所以 `pip install` 装完
> `uvx` 就有了，不需要单独安装。首次运行会自动下载 `amap-mcp-server`，之后走缓存。

---

## 目录结构

```
smart-trip-planner/
├── backend/
│   ├── run.py                      启动入口
│   ├── requirements.txt
│   ├── .env.example
│   └── app/
│       ├── config.py               配置读取 + 校验
│       ├── agents/
│       │   └── trip_planner_agent.py   ★ 多智能体核心（4 个 Agent + 提示词）
│       ├── api/
│       │   ├── main.py             FastAPI 应用 + CORS
│       │   └── routes/
│       │       ├── trip.py         POST /api/trip/plan   ★ 主接口
│       │       ├── map.py          地图服务接口
│       │       └── poi.py          POI 接口 + 景点配图
│       ├── models/
│       │   └── schemas.py          所有 Pydantic 数据模型
│       └── services/
│           ├── amap_service.py     高德 MCP 封装
│           ├── llm_service.py      LLM 实例
│           └── unsplash_service.py 景点配图
└── frontend/
    ├── index.html / package.json / vite.config.ts / tsconfig.json
    ├── .env.example
    └── src/
        ├── App.vue / main.ts
        ├── services/api.ts         后端接口调用
        ├── types/index.ts          TypeScript 类型
        └── views/
            ├── Home.vue            需求表单
            └── Result.vue          地图 + 行程 + 预算 + PDF 导出
```

---

## API

启动后访问 <http://localhost:8000/docs> 看完整文档。主要端点：

| 方法     | 端点                 | 说明                                     |
| ------ | ------------------ | -------------------------------------- |
| `POST` | `/api/trip/plan`   | **主流程**。入参 `TripRequest`，返回 `TripPlan` |
| `GET`  | `/api/map/poi`     | 按关键词搜 POI                              |
| `GET`  | `/api/map/weather` | 查城市天气                                  |
| `POST` | `/api/map/route`   | 路线规划（walking / driving / transit）      |
| `GET`  | `/api/poi/photo`   | 按景点名从 Unsplash 取图                      |
| `GET`  | `/health`          | 健康检查（用这个，见下方已知限制）                      |

`TripRequest` 的主要字段：

```jsonc
{
  "city": "北京",
  "start_date": "2025-06-01",
  "end_date": "2025-06-03",
  "travel_days": 3,
  "transportation": "公共交通",
  "accommodation": "经济型酒店",
  "preferences": ["历史文化", "美食"],
  "free_text_input": "希望多安排一些博物馆"
}
```

---

## 已知限制

写在这里是因为这些是**当前代码里真实存在的不足**，不是环境问题：

1. **`/api/map/*` 三个接口的响应体解析没写完。** `amap_service.py` 里
   `search_poi` / `get_weather` / `plan_route` / `geocode` / `get_poi_detail`
   五个方法都调了 MCP，但结果解析处仍是 `TODO`，直接返回空。所以这三个接口
   会返回 `success: true` 但 `data` 为空。**主流程 `/api/trip/plan` 不受影响** ——
   它走的是 `trip_planner_agent.py`，是另一条完整实现的路径。

2. **`/api/trip/health` 必然返回 503。** 它访问了 `agent.agent.name`，
   而 `get_trip_planner_agent()` 返回的 `MultiAgentTripPlanner` 并没有 `.agent` 属性
   （它有的是 `attraction_agent` / `weather_agent` / `hotel_agent` / `planner_agent`），
   于是抛 `AttributeError` 被 except 捕获。**用 `/health` 或 `/api/map/health` 做存活检查。**

3. **JSON 解析失败会静默降级成占位行程**（见上文「兜底与它的代价」）。

4. **工具调用协议对格式敏感。** `[TOOL_CALL:...]` 少一个冒号就调不起来，
   且失败时不会报错，只会让模型继续编内容。

5. **强依赖 `hello-agents 0.2.x`。** `1.0.0` 移除了 `MCPTool`、`MemoryTool`、`RAGTool`、
   `NoteTool` 等一批工具，装最新版会直接 `ImportError`。所以 `requirements.txt` 里
   锁定的是 `hello-agents[protocols]>=0.2.4,<=0.2.9`。

---

## License

MIT
