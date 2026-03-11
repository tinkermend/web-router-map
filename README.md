# 网站菜单路由地图构建器

### 2.2 技术栈选型

| 模块         | 技术选型     | 说明                   |
| ------------ | ------------ | ---------------------- |
| Web 框架     | FastAPI      | 高性能异步 API 框架    |
| 浏览器自动化 | Playwright   | 现代浏览器仿真引擎     |
| 任务调度     | APScheduler  | 灵活的 Python 调度框架 |
| 数据验证     | Pydantic     | 数据模型验证           |
| 状态管理     | Redis        | 分布式锁与缓存         |
| 数据存储     | PostgreSQL   | 关系型存储             |
| ORM 框架     | sqlmodel     | SQLORM 框架            |
| 加密         | Cryptography | 敏感信息加密           |
| 日志         | Loguru       | 结构化日志管理         |
| 监控         | Sentry       | 异常监控与报警         |
| 验证码       | ddddocr      | 验证码识别             |
| mcp框架      | MCP          | mcp服务能力             |


### 项目运行

```bash
uv run uvicorn src.main:app --host 0.0.0.0 --port 8000 --workers 1

```

### 测试系统

- `name:` Vben Admin
- `url:` https://ele.vben.pro/#/auth/login
- `account:` vben
- `password:` 123456
- `auth:` 滑块验证码

---

- `name:` Gin Vue Admin
- `url:` https://demo.gin-vue-admin.com/#/login
- `account:` admin
- `password:` 123456
- `auth:` 图形验证码

---

- `name:` Vue Element Admin
- `url:` https://panjiachen.github.io/vue-element-admin/#/login?redirect=%2Fdashboard
- `account:` admin
- `password:` 123456
- `auth:` 无

---

- `name:` Pure Admin
- `url:` https://pure-admin.github.io/vue-pure-admin/#/login
- `account:` admin
- `password:` admin123
- `auth:` 图形验证码

---

- `name:` Soybean
- `url:` https://soybeanjs.cn/login/pwd-login?redirect=/function/tab
- `account:` Soybean
- `password:` 123456
- `auth:` 无

## StorageState / Cookie 刷新

- API:
  - `POST /api/auth/refresh/{sys_code}`: 触发登录并更新 `storage_states`
  - `GET /api/auth/state/{sys_code}`: 查询当前有效状态
  - `POST /api/auth/manual-state/{sys_code}`: 手动注入状态快照
  - `GET /api/tasks/{sys_code}/logs`: 查询认证/采集任务状态历史
- CLI:
  - `python scripts/refresh-storage-state.py --sys-code ele.vben.pro --headed`

### login_auth 处理规则

- 认证挑战分发只读取 `web_systems.login_auth` 字段：
  - `captcha_slider`：滑块验证码（`ddddocr.slide_match`）
  - `captcha_image`：图形验证码（`ddddocr.classification`）
  - `captcha_click`：点选验证码（`ddddocr.detection + classification`）
  - `captcha_sms`、`sso`：当前未实现，触发明确报错（fail-fast）
  - `none`：不处理验证码

### login_selectors 推荐配置（captcha 子对象）

```json
{
  "username": "#username",
  "password": "#password",
  "submit": "button[type='submit']",
  "captcha": {
    "slider": {
      "track": "#slider-track",
      "handle": ".slider-handle",
      "hint": ".slider-hint"
    },
    "image": {
      "image": ".captcha-image",
      "input": "input[name='captcha']",
      "refresh": ".captcha-refresh",
      "error": ".captcha-error"
    },
    "click": {
      "image": ".click-captcha-image",
      "prompt": ".click-captcha-prompt",
      "refresh": ".click-captcha-refresh",
      "confirm": ".click-captcha-confirm",
      "error": ".click-captcha-error"
    }
  }
}
```

- 兼容说明：系统会优先读取 `captcha.*`，同时兼容历史扁平字段（例如 `captcha_image`、`captcha_input`、`captcha_click_image` 等）。
- `slider.hint` 为可选：优先使用数据库配置；未配置时会在滑块区域内自动探测提示元素，探测失败会给出明确报错并提示补齐配置。

## 菜单地图采集（DB 状态驱动）

- API:
  - `POST /api/crawl/run/{sys_code}`: 使用 DB 中最新有效 `storage_states` 触发采集并写入 `nav_menus/app_pages/ui_containers/ui_elements`
- CLI:
  - `python scripts/crawl-menu-map-from-db.py --sys-code ele.vben.pro --max-pages 10`
- 说明:
  - 采集器复用 `scripts/crawl-menu-map.py` 能力，服务层负责状态加载、失效触发认证、结果入库与统计日志。

## AI 扁平化上下文字段（新增）

- `nav_menus`: `node_path`、`source`、`is_ai_primary_candidate`、`ai_candidate_rank`
- `app_pages`: `page_summary`、`keywords`、`actionable_element_count`、`elements_raw_count`、`elements_filtered_out_count`
- `ui_elements`: `dom_css_path`、`locator_tier`、`stability_score`、`is_global_chrome`、`is_business_useful`、`usage_description`
