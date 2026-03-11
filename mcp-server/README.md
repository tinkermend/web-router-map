# menu-context-mcp-server

独立部署的 MCP 服务（基于 [PrefectHQ/fastmcp](https://github.com/PrefectHQ/fastmcp)），用于从 `web-router-map` 采集库中检索 AI 可执行上下文（系统 -> 页面 -> 元素）。

---

## 快速启动

### 使用 uv（推荐）

```bash
cd mcp-server
uv sync
cp .env.example .env
uv run menu-context-mcp
```

### 使用 pip

```bash
cd mcp-server
python -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
menu-context-mcp
```

默认通过 `stdio` 运行。若要 HTTP：

```bash
# uv
MCP_TRANSPORT=streamable-http MCP_HOST=0.0.0.0 MCP_PORT=8765 uv run menu-context-mcp

# pip
MCP_TRANSPORT=streamable-http MCP_HOST=0.0.0.0 MCP_PORT=8765 menu-context-mcp
```

---

## AI 工具集成配置

### Claude Desktop

编辑 `~/Library/Application Support/Claude/claude_desktop_config.json`（macOS）或 `%APPDATA%\Claude\claude_desktop_config.json`（Windows）：

```json
{
  "mcpServers": {
    "web-router-map-context": {
      "command": "uv",
      "args": ["--directory", "/path/to/mcp-server", "run", "menu-context-mcp"],
      "env": {
        "MCP_DATABASE_URL": "postgresql+asyncpg://user:pass@localhost:5432/navai",
        "MCP_DATABASE_SCHEMA": "navai"
      }
    }
  }
}
```

### Cursor / Windsurf

在项目根目录创建 `.cursor/mcp.json` 或 `.windsurf/mcp.json`：

```json
{
  "mcpServers": {
    "web-router-map-context": {
      "command": "uv",
      "args": ["--directory", "/path/to/mcp-server", "run", "menu-context-mcp"],
      "env": {
        "MCP_DATABASE_URL": "postgresql+asyncpg://user:pass@localhost:5432/navai",
        "MCP_DATABASE_SCHEMA": "navai"
      }
    }
  }
}
```

### HTTP 模式（适用于远程部署）

启动服务：

```bash
MCP_TRANSPORT=streamable-http MCP_HOST=0.0.0.0 MCP_PORT=8765 menu-context-mcp
```

客户端配置（以 Claude Desktop 为例）：

```json
{
  "mcpServers": {
    "web-router-map-context": {
      "url": "http://your-server:8765/mcp"
    }
  }
}
```

---

## 提供的 MCP Tools

### 1. `get_page_playwright_context`（主工具）

获取 AI 可执行的系统/页面/定位器上下文，返回 Top-1 页面 + Top-2 备选页面 + 5-15 条稳定定位器。

#### 入参

| 参数                  | 类型   | 必填   | 默认值 | 说明                                    |
| --------------------- | ------ | ------ | ------ | --------------------------------------- |
| `system_keyword`      | string | **是** | -      | 系统关键词（如 "ERP"、"A 系统"）        |
| `page_keyword`        | string | 否     | -      | 页面关键词（如 "用户管理"、"订单列表"） |
| `menu_keyword`        | string | 否     | -      | 菜单关键词（与 page_keyword 二选一）    |
| `route_hint`          | string | 否     | -      | 路由提示（如 "/admin/users"）           |
| `max_locators`        | int    | 否     | 10     | 返回定位器数量（5-15）                  |
| `max_fallback_pages`  | int    | 否     | 2      | 备选页面数量（0-2）                     |
| `min_stability_score` | float  | 否     | 0.7    | 定位器最低稳定度阈值（0-1）             |
| `freshness_hours`     | int    | 否     | 168    | 数据新鲜度阈值（小时）                  |
| `include_debug_trace` | bool   | 否     | false  | 是否返回调试追踪信息                    |

> **约束**：`page_keyword`、`menu_keyword`、`route_hint` 至少提供一个。

#### 出参

```json
{
  "status": "ok",
  "stale_context": false,
  "system": {
    "sys_code": "erp_sys_a",
    "name": "A系统-内部ERP",
    "base_url": "https://erp.example.com",
    "framework_type": "vue3",
    "health_status": "online",
    "state_valid": true
  },
  "target_page": {
    "menu_id": "uuid-xxx",
    "page_id": "uuid-yyy",
    "title": "用户管理",
    "text_breadcrumb": "系统设置 > 权限管理 > 用户管理",
    "route_path": "/admin/users",
    "target_url": "https://erp.example.com/admin/users",
    "menu_node_type": "page",
    "last_verified_status": "200",
    "score": 0.92,
    "recall_stage": "exact"
  },
  "navigation_plan": {
    "strategy": "menu_chain_then_route_fallback",
    "target_menu_id": "uuid-xxx",
    "click_sequence": ["系统设置", "权限管理", "用户管理"],
    "breadcrumb_chain": ["系统设置", "权限管理", "用户管理"],
    "steps": [
      {
        "menu_id": "uuid-root",
        "title": "系统设置",
        "level": 0,
        "is_target": false,
        "playwright_locator": "get_by_text('系统设置')"
      },
      {
        "menu_id": "uuid-xxx",
        "title": "用户管理",
        "level": 2,
        "is_target": true,
        "route_path": "/admin/users",
        "target_url": "https://erp.example.com/admin/users",
        "playwright_locator": "get_by_text('用户管理')"
      }
    ],
    "route_path": "/admin/users",
    "target_url": "https://erp.example.com/admin/users",
    "route_fallback_enabled": true
  },
  "locators": [
    {
      "element_type": "action_btn",
      "text_content": "新增用户",
      "nearby_text": "用户列表",
      "playwright_locator": "get_by_role('button', name='新增用户')",
      "stability_score": 0.95,
      "locator_tier": "priority_1",
      "usage_description": "点击打开新增用户弹窗"
    }
  ],
  "fallback_pages": [
    {
      "menu_id": "uuid-zzz",
      "title": "角色管理",
      "text_breadcrumb": "系统设置 > 权限管理 > 角色管理",
      "route_path": "/admin/roles",
      "score": 0.78,
      "recall_stage": "fuzzy"
    }
  ],
  "freshness": {
    "last_crawl_at": "2024-01-15T10:30:00Z",
    "page_crawled_at": "2024-01-15T10:35:00Z",
    "freshness_hours": 168
  },
  "constraints": {
    "use_only_provided_locators": true,
    "verify_before_execute": true,
    "prefer_navigation_plan": true
  },
  "reasons": []
}
```

#### 状态码说明

| status             | 说明                       |
| ------------------ | -------------------------- |
| `ok`               | 成功找到匹配的页面和定位器 |
| `system_not_found` | 未找到匹配的系统           |
| `page_not_found`   | 未找到匹配的页面           |
| `need_recrawl`     | 数据陈旧，建议触发重新采集 |

---

### 2. `get_menu_interaction_context`（兼容别名）

面向菜单交互场景的简化接口，等同于 `get_page_playwright_context`。

#### 入参

| 参数                  | 类型   | 必填   | 默认值 | 说明           |
| --------------------- | ------ | ------ | ------ | -------------- |
| `system_keyword`      | string | **是** | -      | 系统关键词     |
| `menu_keyword`        | string | **是** | -      | 菜单关键词     |
| `route_hint`          | string | 否     | -      | 路由提示       |
| `max_locators`        | int    | 否     | 10     | 返回定位器数量 |
| `include_debug_trace` | bool   | 否     | false  | 调试开关       |

#### 出参

同 `get_page_playwright_context`。

---

### 3. `get_page_navigation_plan`（导航链路专用）

获取页面的父子菜单导航计划，返回 `navigation_plan`（父级点击顺序、逐级 locator、route fallback）。

#### 入参

| 参数                  | 类型   | 必填   | 默认值 | 说明                                    |
| --------------------- | ------ | ------ | ------ | --------------------------------------- |
| `system_keyword`      | string | **是** | -      | 系统关键词                               |
| `page_keyword`        | string | 否     | -      | 页面关键词                               |
| `menu_keyword`        | string | 否     | -      | 菜单关键词（与 page_keyword 二选一）     |
| `route_hint`          | string | 否     | -      | 路由提示                                 |
| `max_fallback_pages`  | int    | 否     | 2      | 备选页面数量（0-2）                      |
| `min_stability_score` | float  | 否     | 0.7    | 定位器最低稳定度阈值（0-1）              |
| `freshness_hours`     | int    | 否     | 168    | 数据新鲜度阈值（小时）                   |
| `include_debug_trace` | bool   | 否     | false  | 是否返回调试追踪信息                     |

#### 出参

与 `get_page_playwright_context` 相同，但推荐优先消费 `navigation_plan` 用于菜单点击链路执行。

---

### 4. `get_storage_state_for_session`（会话复用）

获取浏览器存储状态（cookies、localStorage、sessionStorage），用于 Playwright 脚本跳过登录，直接复用已有会话。

#### 入参

| 参数          | 类型   | 必填   | 默认值 | 说明                                  |
| ------------- | ------ | ------ | ------ | ------------------------------------- |
| `system_name` | string | **是** | -      | 系统名称（模糊匹配 web_systems.name） |

#### 出参

```json
{
  "status": "ok",
  "system": {
    "sys_code": "erp_sys_a",
    "name": "A系统-内部ERP",
    "base_url": "https://erp.example.com",
    "framework_type": "vue3",
    "health_status": "online",
    "state_valid": true
  },
  "state": {
    "cookies": [
      {
        "name": "session_id",
        "value": "xxx",
        "domain": "erp.example.com",
        "path": "/",
        "httpOnly": true,
        "secure": true
      }
    ],
    "storage_state": {
      "cookies": [...],
      "origins": [
        {
          "origin": "https://erp.example.com",
          "localStorage": [
            { "name": "token", "value": "eyJhbGciOiJIUzI1NiIs..." }
          ]
        }
      ]
    },
    "local_storage": {
      "token": "eyJhbGciOiJIUzI1NiIs...",
      "user_info": "{...}"
    },
    "session_storage": {}
  },
  "state_id": "uuid-xxx",
  "is_valid": true,
  "validated_at": "2024-01-15T10:30:00Z",
  "expires_at": "2024-01-16T10:30:00Z",
  "auth_mode": "bearer",
  "reasons": [],
  "usage_hint": "Use storage_state with browser.new_context(storage_state=response.state.storage_state); Navigate to https://erp.example.com after context creation to restore session"
}
```

#### 状态码说明

| status             | 说明                   |
| ------------------ | ---------------------- |
| `ok`               | 成功获取有效的存储状态 |
| `system_not_found` | 未找到匹配的系统       |
| `no_valid_state`   | 系统无有效的存储状态   |
| `state_expired`    | 存储状态已过期         |

#### Playwright 使用示例

```python
# 1. 调用 MCP tool 获取存储状态
response = await mcp.call_tool("get_storage_state_for_session", {"system_name": "ERP"})

# 2. 使用返回的 storage_state 创建浏览器上下文
from playwright.async_api import async_playwright

async with async_playwright() as p:
    browser = await p.chromium.launch()

    # 直接注入存储状态，跳过登录
    context = await browser.new_context(
        storage_state=response["state"]["storage_state"]
    )

    page = await context.new_page()

    # 直接访问需要登录的页面
    await page.goto(response["system"]["base_url"] + "/admin/users")

    # 此时已处于登录状态，可直接操作
    await page.get_by_role("button", name="新增用户").click()
```

---

## 检索策略

### 召回流程

```
系统匹配 → 页面召回 → 定位器过滤 → 统一打分 → Top-K 返回
```

### 三段召回

| 阶段       | 策略     | 说明                                           |
| ---------- | -------- | ---------------------------------------------- |
| `exact`    | 精确匹配 | `title = keyword` 或 `route_path = route_hint` |
| `fuzzy`    | 模糊匹配 | `ILIKE '%keyword%'` 或 `text_breadcrumb` 包含  |
| `semantic` | 语义兜底 | PostgreSQL 全文检索（FTS）                     |

### 统一打分公式

```
total_score =
    0.25 × system_match_score +
    0.30 × page_text_match_score +
    0.20 × route_match_score +
    0.15 × freshness_score +
    0.10 × locator_stability_score
```

### 定位器过滤规则

- 仅保留 `element_type` 为 `action_btn` / `form_input` / `nav_link` 的元素
- `stability_score >= min_stability_score`（默认 0.7）
- 过滤全局壳层噪声（header/footer/sidebar 等）

---

## 环境变量

| 变量                           | 默认值                   | 说明                                                   |
| ------------------------------ | ------------------------ | ------------------------------------------------------ |
| `MCP_DATABASE_URL`             | -                        | PostgreSQL 连接字符串                                  |
| `MCP_DATABASE_SCHEMA`          | `navai`                  | 数据库 Schema                                          |
| `MCP_MIN_STABILITY_SCORE`      | `0.7`                    | 定位器稳定度阈值                                       |
| `MCP_FRESHNESS_HOURS`          | `168`                    | 数据新鲜度阈值（小时）                                 |
| `MCP_MAX_CANDIDATES_PER_STAGE` | `20`                     | 每阶段最大候选数                                       |
| `MCP_SERVER_NAME`              | `web-router-map-context` | MCP 服务名称                                           |
| `MCP_TRANSPORT`                | `stdio`                  | 传输模式：`stdio` / `http` / `sse` / `streamable-http` |
| `MCP_HOST`                     | `127.0.0.1`              | HTTP 监听地址                                          |
| `MCP_PORT`                     | `8765`                   | HTTP 监听端口                                          |
| `MCP_LOG_LEVEL`                | `INFO`                   | 日志级别                                               |

---

## 调用示例

### 示例 1：查询用户管理页面

**请求**

```json
{
  "system_keyword": "ERP",
  "page_keyword": "用户管理"
}
```

**用途**：获取 ERP 系统中用户管理页面的定位器，用于自动化测试。

### 示例 2：通过路由精确查询

**请求**

```json
{
  "system_keyword": "A系统",
  "route_hint": "/orders/finance/write-off"
}
```

**用途**：精确获取订单核销页面的上下文。

### 示例 3：菜单交互查询

**请求**

```json
{
  "system_keyword": "内部ERP",
  "menu_keyword": "订单核销",
  "include_debug_trace": true
}
```

**用途**：获取菜单交互上下文，包含调试追踪信息。

---

## 返回上下文结构

```
├── status              # 状态码
├── stale_context       # 是否陈旧
├── system              # 系统上下文
│   ├── sys_code
│   ├── name
│   ├── base_url
│   ├── framework_type
│   ├── health_status
│   └── state_valid
├── target_page         # Top-1 主候选页面
│   ├── menu_id
│   ├── title
│   ├── text_breadcrumb
│   ├── route_path
│   ├── target_url
│   ├── score
│   └── recall_stage
├── locators[]          # 5-15 条高稳定度定位器
│   ├── element_type
│   ├── text_content
│   ├── playwright_locator
│   ├── stability_score
│   └── usage_description
├── fallback_pages[]    # 最多 2 条备选页面
├── freshness           # 新鲜度信息
├── constraints         # 执行约束
├── reasons             # 原因说明
└── debug_trace         # 调试追踪（可选）
```

---

## 执行约束说明

返回的 `constraints` 字段包含 AI 执行 Playwright 脚本时必须遵循的约束：

| 约束                          | 说明                             |
| ----------------------------- | -------------------------------- |
| `only_use_provided_locators`  | 仅使用返回的定位器，禁止自行猜测 |
| `verify_url_after_navigation` | 导航后验证 URL 是否符合预期      |
| `retry_on_locator_failure`    | 定位器失败时使用 fallback_pages  |

---

## 开发与测试

```bash
# 安装开发依赖（uv）
uv sync --extra dev

# 安装开发依赖（pip）
pip install -e ".[dev]"

# 运行测试
uv run pytest tests/
# 或
pytest tests/

# 类型检查
uv run mypy src/
# 或
mypy src/
```
