# 本地真实短时演练 Runbook

这份手册用于在本地 Mac 上安全完成 5-10 分钟真实 iFind 行情 + 飞书通知演练。

本阶段不是正式长期盯盘。目标是短时间验证链路是否健康，并在出现异常时能快速暂停或退出。

## 演练目标

本地真实短时演练用于确认：

- iFind 行情链路可以获取最新价格。
- 飞书通知链路可以收到测试消息。
- 后台 worker 能正常自动轮询。
- AlertLog 能合理记录报警历史。
- `/healthz` 能返回当前运行状态。
- Dashboard 能正确显示运行、演练、报警和诊断状态。

## 演练前准备

开始前请逐项确认：

- 工作区没有未确认的临时改动，先查看 `git status`。
- 本地配置文件不要提交到 Git。
- `MARKET_DATA_MODE=ifind`。
- `NOTIFICATION_MODE=feishu`。
- 至少有一个启用中的监控任务。
- 监控任务的 `base_price` 合理，不要和真实行情相差过大。
- 监控任务的 `grid_size` / `threshold` 大于 0，且符合演练预期。
- Dashboard 中“测试 iFind 行情”已经成功。
- Dashboard 中“测试飞书通知”已经成功。
- 如果暂时不想自动盯盘，先在 Dashboard 点击“暂停自动监控”。

## 启动命令

在本地终端运行：

```bash
cd /Users/zapkissy/Desktop/grid_monitor
source .venv/bin/activate
MARKET_DATA_MODE=ifind flask --app main run --port 5001
```

不要把任何真实授权值、飞书机器人地址或签名密钥写入文档、聊天记录或 Git 提交。

## 演练步骤

按下面顺序执行：

- 打开 `http://127.0.0.1:5001`。
- 查看 Dashboard 顶部“运行与演练状态”。
- 对启用任务点击“测试 iFind 行情”。
- 点击“测试飞书通知”。
- 打开 `http://127.0.0.1:5001/healthz`，确认状态可读。
- 如需自动演练，确认自动监控为启用且未暂停。
- 观察 5-10 分钟。
- 同时观察终端日志、Dashboard 报警历史和飞书消息。
- 如果出现异常，立即点击“暂停自动监控”，或在终端按 `Control + C` 停止 Flask。

## 演练中重点观察

重点看这些现象：

- 是否持续获取到 iFind 最新行情。
- 是否出现 `Market price skipped`。
- 是否出现授权换取失败或访问凭证不可用。
- 是否出现飞书发送失败。
- 是否出现完全重复报警被跳过。
- 是否出现高频报警提示。
- `base_price` 是否按 Dynamic Grid Rebase 预期变化。
- AlertLog 是否只记录合理的网格突破。
- `/healthz` 中最近轮询、最近报警、最近通知状态是否符合预期。

## 常见问题

### 某个合约 iFind 返回 input parameters 错误

可能是合约格式、交易所后缀或合约映射配置不匹配。

处理建议：

- 先在 Dashboard 对该合约单独点击“测试 iFind 行情”。
- 检查 `IFIND_CODE_MAP`、`IFIND_EXCHANGE_SUFFIX_MAP` 或 `IFIND_CODE_SUFFIX` 是否符合交易所要求。
- 也可以直接在任务合约代码中填写完整 iFind 代码，例如 `Y2609.DCE` 或 `CU2608.SHF`。
- 如果合约代码已经带英文点号，系统会直接使用它，不再追加统一后缀。
- 精确映射优先，其次按品种前缀映射，最后才使用统一兜底后缀。
- 具体后缀以 iFind 实际可用格式为准，可以用“测试 iFind 行情”按钮验证。
- 暂时停用该任务，避免影响其他合约演练。
- 后续单独修复该合约映射。

### 飞书没有收到测试消息

处理建议：

- 确认 `NOTIFICATION_MODE=feishu`。
- 先点击 Dashboard 顶部的“测试飞书通知”。
- 检查本地飞书机器人地址配置是否正确。
- 如启用签名，检查签名密钥配置是否正确。
- 查看 Dashboard 运行诊断里的通知状态。

### 自动监控没有动作

处理建议：

- 确认至少有一个任务处于启用状态。
- 确认 `MARKET_DATA_MODE=ifind`。
- 确认 Dashboard 显示后台 worker 已启动。
- 确认自动监控没有被暂停。
- 查看 `/healthz` 中的最近轮询状态和说明。

### 报警太多

处理建议：

- 立即暂停自动监控。
- 检查 `base_price` 是否离当前真实行情太远。
- 检查 `grid_size` / `threshold` 是否过小。
- 查看报警历史，确认是否存在高频报警提示。
- 调整任务参数后再重新短时演练。

## 演练结束收尾

结束时请做这些事：

- 在终端按 `Control + C` 停止 Flask。
- 查看 `git status`，确认没有误提交本地配置文件。
- 如需复盘，在 Dashboard 报警历史中导出 CSV。
- 记录发现的问题，包括合约代码、时间、错误摘要和处理动作。
- 不要提交任何本地授权、机器人地址或签名密钥。
