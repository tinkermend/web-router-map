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
| 验证码       | dddddocr     | 验证码识别             |

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
- CLI:
  - `python scripts/refresh-storage-state.py --sys-code ele.vben.pro --headed`

## 菜单地图采集（DB状态驱动）

- API:
  - `POST /api/crawl/run/{sys_code}`: 使用 DB 中最新有效 `storage_states` 触发采集并写入 `nav_menus/app_pages/ui_containers/ui_elements`
- CLI:
  - `python scripts/crawl-menu-map-from-db.py --sys-code ele.vben.pro --max-pages 10`
- 说明:
  - 采集器复用 `scripts/crawl-menu-map.py` 能力，服务层负责状态加载、失效触发认证、结果入库与统计日志。
