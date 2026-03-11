# Changelog

## 2026-03-11

- 新增 `scripts/generate_and_run.py`：按自然语言对话解析检测意图，调用 MCP 上下文检索（`get_page_playwright_context` / `get_storage_state_for_session`）并在 Playwright 有头模式执行页面检测，输出 JSON 报告与步骤截图。
- 新增 `generate_and_run` 相关单测到 `src_tests/test_verify_storage_state_reuse_script.py`：覆盖对话意图解析、系统关键词推断、定位器表达式解析与首页定位器选择逻辑。
- 调整 `scripts/generate_and_run.py` 默认执行参数为快速配置：`--timeout-ms` 从 `30000` 下调到 `8000`，`--slow-mo` 从 `300` 下调到 `50`，减少单次三对话巡检耗时。
- 优化 `scripts/generate_and_run.py` 的系统识别策略：移除无系统名时默认回退到“滑动窗口系统”的推测逻辑；当 `system_not_found` 时返回明确 `user_hint` 要求用户提供正确系统名称，并新增对应单测覆盖“缺失系统名报错”场景。
- 修复 `AuthCrawler._wait_login_success` 认证成功判定误报：移除“仍在登录页但仅凭 cookie/token 信号即判定成功”的分支，改为必须先离开登录页，避免未登录态被错误持久化。
- 修复 `crawl-menu-map.py` 在 history 路由场景下的 URL 组装：`_build_url_from_route` 现在按 `origin + route_path` 构造目标地址，避免错误拼接为 `.../dashboard/...` 导致页面抓取偏移。
- 修复 `crawl-menu-map.py` 的登录态校验回归：`_is_state_valid` 在 URL 判定之外恢复登录表单特征识别，避免“无 `/login` 路径但实际处于登录页”被误判为有效会话。
- 调整 `AuthService._save_state` 的 `storage_states` 持久化策略：优先更新同一 `web_system` 的已有会话记录（`update`），仅在首次无记录时兜底创建，避免持续插入历史 token 快照。
- 新增单测覆盖 `storage_states` 复用场景，验证连续保存认证状态时不会新增多条记录，并保持 `web_systems.latest_valid_state_id` 指向同一会话记录。

## 2026-03-10

- 新增基于 `SQLModel + asyncpg` 的数据库公共基础设施层：配置加载（`pydantic-settings`）、异步引擎与会话工厂、事务上下文、健康检查、初始化（schema/extension）与资源释放。
- 新增 API 侧数据库依赖注入入口 `get_db`，统一复用公共会话生成逻辑。
- 新增 `pytest` 用例覆盖配置默认值/环境覆盖，以及数据库基础能力（连通性、事务提交回滚、初始化幂等）。
