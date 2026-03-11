# Changelog

## 2026-03-11

- 调整 `AuthService._save_state` 的 `storage_states` 持久化策略：优先更新同一 `web_system` 的已有会话记录（`update`），仅在首次无记录时兜底创建，避免持续插入历史 token 快照。
- 新增单测覆盖 `storage_states` 复用场景，验证连续保存认证状态时不会新增多条记录，并保持 `web_systems.latest_valid_state_id` 指向同一会话记录。

## 2026-03-10

- 新增基于 `SQLModel + asyncpg` 的数据库公共基础设施层：配置加载（`pydantic-settings`）、异步引擎与会话工厂、事务上下文、健康检查、初始化（schema/extension）与资源释放。
- 新增 API 侧数据库依赖注入入口 `get_db`，统一复用公共会话生成逻辑。
- 新增 `pytest` 用例覆盖配置默认值/环境覆盖，以及数据库基础能力（连通性、事务提交回滚、初始化幂等）。
