# Changelog

## v0.1.0-local-stable

状态：

- 本地可运行稳定版。
- 支持 iFind 行情模式。
- 支持飞书通知。
- 支持自动监控。
- 支持动态网格更新。
- 支持报警历史与 CSV 导出。
- Dashboard UI 已完成一轮整理。
- pytest warnings 已清理。

已完成模块：

- Flask 项目结构。
- 数据库模型。
- Dashboard CRUD。
- Market provider abstraction。
- iFind token manager。
- iFind real_time_quotation provider。
- Monitor worker。
- Dynamic Grid Rebase。
- AlertLog。
- Feishu notifier。
- Notification diagnostics。
- Burst protection。
- Preflight checks。
- Live mode guidance。
- Alert history filters / pagination / CSV export。
- Dashboard auto refresh。
- Contract code suffix support。
- UI polish。
- Warnings cleanup。

待开盘复测：

- iFind + Feishu 真实模式长时间观察 30-60 分钟。
- 多次真实轮询连续稳定性。
- 真实突破时 AlertLog / base_price / Feishu 三者一致性。
