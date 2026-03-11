## 系统整体架构设计

系统采用分层架构设计，各司其职，保证系统的可插拔性与高容错性。

- API 接口层 (FastAPI): 提供配置录入（目标 URL、账密、框架类型、调度周期等）、手动 Token 注入接口、以及系统状态探针接口。
- 调度中心 (APScheduler): 系统的心脏，负责独立调度「Token/状态刷新任务」与「菜单地图抓取任务」，并在多任务并发时触发锁请求。
- 执行引擎层 (Playwright/Python): 核心采集层，包含登录态生成器（StorageState Generator）与路由提取器（Route Extractor）。
- 数据处理层 (Pydantic / Cryptography): 负责入库前的数据校验、清洗、转换，以及对 Web 系统登录凭证的对称加密/解密。
- 存储与缓存层 (PostgreSQL / Redis): PG 用于落盘最终的 AI 友好型持久化数据；Redis 提供分布式锁，防止同一目标的采集任务产生并发冲突。
- 基础设施与监控层 (Loguru / Sentry): 提供全局结构化日志记录与应用级崩溃监控。

## 基于前端框架特征的路由提取策略

为了实现高效且精确的抓取，系统优先采用“注入审查”模式，次选“仿真遍历”模式。

- Console 实例探测提取 (首选方案):

  - Vue 环境: 通过 Playwright Page 注入 JS 脚本，探测 window.**VUE_ROUTER** (Vue3) 或分析根挂载实例 document.querySelector('#app').**vue**.$router.options.routes (Vue2)。一旦获取到路由配置对象，直接递归解析生成完整的路由结构。
  - React 环境: 探测 React DevTools 暴露的全局钩子 **REACT_DEVTOOLS_GLOBAL_HOOK** 或根节点 \_reactRootContainer，解析其 Fiber 树提取内部定义的 Routes。

- DOM 仿真抓取识别 (降级兜底方案): 对于路由被高度混淆或无法直接反射的系统，通过 Playwright 定位侧边栏 (aside, nav, menu 等高频标签)，进行递归点击展开，并监听 URL 变化与 DOM 渲染，动态生成路径数。

## 强制约束

- 每次开发完成进行单元测试
- 每一次变更都变更总结到 CHANGELOG.md 中

项目启动命令: uv run uvicorn src.main:app --host 0.0.0.0 --port 8000 --workers 1
