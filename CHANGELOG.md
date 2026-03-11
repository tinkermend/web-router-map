# Changelog

## 2026-03-11

- 优化 `playwright-script-generator/scripts/generate_and_run.py`（及仓库镜像脚本 `scripts/generate_and_run.py`）页面关键词处理：新增轻量 `extract_page_keyword`，可从自然语言整句中提取叶子页面词（如从“超级管理员下的菜单管理页面数据表格是否正常展现”提取“菜单管理”），并在调用 MCP 前自动归一化关键词。
- 修复 `scripts/generate_and_run.py` 的兼容性回归：恢复 `parse_args`/`parse_dialogues`/`_parse_locator`/`_pick_home_locator`/`_normalize_dialogue_text` 等模块级入口，避免被单测与外部调用方导入时触发 `AttributeError`。
- 修复 `scripts/generate_and_run.py` 的 route-only 检测误判：当 `target_url` 为空但 `route_path` 或 `navigation_plan` 可用时不再提前报错，改为继续执行菜单链路与运行时路由回退。
- 修复 `AuthCrawler._wait_login_success` 登录页识别误判：`startsWith(login_url)` 前缀匹配仅基于 path/fragment 登录语义启用，并改进登录关键词匹配边界，避免 `login.example.com` 与 `cataloging` 这类非登录路径触发超时误判。
- 修复 `mcp-server/src/menu_context_mcp/server.py` 的 `get_page_navigation_plan` 工具入参兼容性：补充 `max_locators` 参数并透传到 `ContextQuery`，避免 `playwright-script-generator` 调用时报 `Unexpected keyword argument: max_locators` 后回退旧工具。
- 调整 `playwright-script-generator/scripts/generate_and_run.py` 调用链路：页面上下文优先调用 `get_page_navigation_plan`（失败回退 `get_page_playwright_context`），并在执行侧优先消费 `navigation_plan.steps[].playwright_locator` 进行菜单导航，移除本地菜单文本猜测/展开逻辑，仅保留基于目标路由的运行时回退。
- 修复 `mcp-server` 导航链路查询 SQL 在 asyncpg 下的参数类型语法问题：将 `:menu_id::uuid` 改为 `CAST(:menu_id AS uuid)`，恢复 `fetch_navigation_chain` 正常执行。
- 新增 `mcp-server` 导航链路能力：`get_page_playwright_context` 出参增加 `navigation_plan`（父级菜单点击序列、逐级 locator、route fallback），并新增专用工具 `get_page_navigation_plan` 以便执行端在“仅提供叶子页面名”时稳定还原父子菜单点击路径；同步更新 `mcp-server` 文档与测试覆盖。
- 优化 `playwright-script-generator/scripts/generate_and_run.py` 的“仅菜单名”定位能力：当仅提供叶子页面名（如“菜单管理”）时，先尝试侧边栏父级展开与菜单点击，失败后回退到前端运行时路由推送（`router.push`/`hash`），在不增加 MCP 查询次数（仍为 1 次页面上下文 + 1 次会话态）的前提下提升命中率。
- 修复 `playwright-script-generator/scripts/generate_and_run.py` 页面健康检测的导航回退逻辑：当 `base_url -> target_url` 未命中时，新增按“菜单路径（如 超级管理员 -> 菜单管理）”逐级点击的二次定位流程，并将“URL 命中”与“元素命中”拆分为可访问性与置信度两维判定（新增 `low_confidence` 状态），避免因未点击菜单导致误判页面异常。
- 新增 `scripts/generate_and_run.py`：按自然语言对话解析检测意图，调用 MCP 上下文检索（`get_page_playwright_context` / `get_storage_state_for_session`）并在 Playwright 有头模式执行页面检测，输出 JSON 报告与步骤截图。
- 新增 `generate_and_run` 相关单测到 `src_tests/test_verify_storage_state_reuse_script.py`：覆盖对话意图解析、系统关键词推断、定位器表达式解析与首页定位器选择逻辑。
- 调整 `scripts/generate_and_run.py` 默认执行参数为快速配置：`--timeout-ms` 从 `30000` 下调到 `8000`，`--slow-mo` 从 `300` 下调到 `50`，减少单次三对话巡检耗时。
- 优化 `scripts/generate_and_run.py` 的系统识别策略：移除无系统名时默认回退到“滑动窗口系统”的推测逻辑；当 `system_not_found` 时返回明确 `user_hint` 要求用户提供正确系统名称，并新增对应单测覆盖“缺失系统名报错”场景。
- 修复 `AuthCrawler._wait_login_success` 认证成功判定误报：移除“仍在登录页但仅凭 cookie/token 信号即判定成功”的分支，改为必须先离开登录页，避免未登录态被错误持久化。
- 修复 `AuthCrawler._wait_login_success` 登录页前缀误判：仅当 `login_url`/path/fragment 明确包含登录语义（`login`/`signin`）时才启用 `startsWith(login_url)` 判定，避免 `login_url` 为 `#/` 应用根时将 `#/dashboard` 误判为仍在登录页导致认证超时。
- 修复 `crawl-menu-map.py` 在 history 路由场景下的 URL 组装：`_build_url_from_route` 现在按 `origin + route_path` 构造目标地址，避免错误拼接为 `.../dashboard/...` 导致页面抓取偏移。
- 修复 `crawl-menu-map.py` 的登录态校验回归：`_is_state_valid` 在 URL 判定之外恢复登录表单特征识别，避免“无 `/login` 路径但实际处于登录页”被误判为有效会话。
- 调整 `AuthService._save_state` 的 `storage_states` 持久化策略：优先更新同一 `web_system` 的已有会话记录（`update`），仅在首次无记录时兜底创建，避免持续插入历史 token 快照。
- 新增单测覆盖 `storage_states` 复用场景，验证连续保存认证状态时不会新增多条记录，并保持 `web_systems.latest_valid_state_id` 指向同一会话记录。
- 修复 `CrawlService.run_by_sys_code` 在“payload 未变化”分支的数据新鲜度更新缺失：现在会同步刷新 `app_pages.crawled_at`（以及 `updated_at`），避免重复采集成功却被 MCP 误判 `need_recrawl`（`stale_context`）；并新增单测覆盖该分支时间戳刷新行为。
- 修复 `scripts/crawl-menu-map.py` 在同步 Playwright 运行时向 `page.evaluate()` 误传 `timeout` 参数导致的路由提取异常：恢复框架探测与 Vue/React 运行时路由注入提取，避免退化到仅 `menu_observe` 的少量页面采集；新增单测覆盖 `evaluate` 参数透传回归。
- 修复 `scripts/crawl-menu-map.py` 元素提取阶段的选择器构造异常：`label[for=...]` 现在对动态 `id` 执行 CSS 转义，避免包含 `{}`/引号等特殊字符时触发 `Page.evaluate` 语法错误并中断整次采集；新增单测覆盖转义逻辑。
- 修复 `CrawlService._persist_payload` 菜单 upsert 的唯一键冲突：新增“按 `route_path` 优先复用历史 `nav_menus` 记录”策略，避免菜单层级/面包屑变动时误判为新节点并触发 `(system_id, route_path)` 重复插入；新增数据库单测覆盖该复用场景。
- 调整菜单采集默认 `max_pages` 从 `10` 提升到 `30`（`CrawlService` 与两条 CLI 入口同步），减少默认任务对二级/深层页面的截断漏采。

## 2026-03-10

- 新增基于 `SQLModel + asyncpg` 的数据库公共基础设施层：配置加载（`pydantic-settings`）、异步引擎与会话工厂、事务上下文、健康检查、初始化（schema/extension）与资源释放。
- 新增 API 侧数据库依赖注入入口 `get_db`，统一复用公共会话生成逻辑。
- 新增 `pytest` 用例覆盖配置默认值/环境覆盖，以及数据库基础能力（连通性、事务提交回滚、初始化幂等）。
