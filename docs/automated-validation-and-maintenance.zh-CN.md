# AKBridge 自动化验收与维护

本文定义 AKBridge 的自动维护边界。所有默认命令均为确定性程序：它们不调用 LLM、不等待人工输入，也不把外部数据源的短暂不可用误判为 MCP 适配回归。

## 分层检查

| 层级 | 命令 | 是否访问第三方数据源 | 失败含义 |
| --- | --- | ---: | --- |
| 本地契约 | `akbridge-maintain ci --strict` | 否 | 发现、Schema、目录或路由契约回归。 |
| 离线逐接口 | `akbridge-accept run --offline` | 否 | 单个接口的本地适配契约未通过。 |
| 数据源探测 | `akbridge-maintain ci --provider` | 是 | 上游网络、反爬、凭据、数据格式或 AKShare 运行时可能变化。 |

前两层应当成为提交和定时 CI 的稳定门禁。第三层建议在独立计划任务中运行并保留验收记录，不应因短暂网络抖动直接覆盖基线。

## 每次升级流程

1. 在隔离分支升级 `akshare` 和锁文件。
2. 运行严格离线门禁，生成新 manifest、语义目录和报告。
3. 检查 `artifacts/maintenance/latest.json`：新增接口是信息项；删除接口、签名变化和 Schema 变化是回归项，严格模式会返回非零退出码。
4. 运行 `--offline` 全量逐接口验收，确认每个接口都仍被发现并可生成 Schema。
5. 需要评估上游可用性时，再运行 `--provider`，按 `ledger.csv` 的错误范围归类问题。
6. 同一主版本的 Dependabot PR 在严格门禁通过后可自动合并；主版本升级或异常变更必须人工审核。
7. 只有明确接受新的兼容性边界后，才更新 `artifacts/acceptance/manifest.json` 作为新基线。

## 命令

生成可审计基线：

```powershell
.venv\Scripts\python.exe -m akbridge.maintenance manifest `
  --output artifacts\acceptance\manifest.json
.venv\Scripts\python.exe -m akbridge.maintenance catalog `
  --output artifacts\catalog.json
```

严格离线检查：

```powershell
.venv\Scripts\python.exe -m akbridge.maintenance ci --strict `
  --baseline artifacts\acceptance\manifest.json `
  --current artifacts\maintenance\manifest.json `
  --catalog artifacts\catalog.json `
  --report artifacts\maintenance\latest.json
```

在定时任务中附加 `--check-latest` 可查询 PyPI 的最新 AKShare 版本。网络不可用时报告状态为 `unavailable`，不会误报适配回归；若需要把发现新版本作为升级告警门禁，可再附加 `--fail-on-update`。

只运行离线逐接口适配验收：

```powershell
.venv\Scripts\python.exe -m akbridge.acceptance run --offline --workers 4 `
  --output artifacts\acceptance\runs\offline.json
.venv\Scripts\python.exe -m akbridge.acceptance report `
  --run artifacts\acceptance\runs\offline.json
```

比较两个 manifest：

```powershell
.venv\Scripts\python.exe -m akbridge.maintenance diff --strict `
  --baseline artifacts\acceptance\manifest.json `
  --current artifacts\maintenance\manifest.json
```

## 退出码与报告

`akbridge-maintain ci --strict` 在以下情况返回非零退出码：

- 当前发现接口数低于最小阈值；
- 接口被删除且超过 `--max-removed`；
- 既有接口的 Python 签名或输入 Schema 哈希变化；
- 目录、输入 Schema 或路由索引验证失败。

报告中包含稳定的 `current_fingerprint`。相同 AKShare 版本和相同本地代码应生成相同指纹；生成时间不参与指纹计算。`metadata_hash` 的变化会被记录，但只有签名和 Schema 变化会被严格门禁视为兼容性回归。

## 上游探测的处理方式

第三方数据源具有验证码、限流、登录、地理网络和临时故障等特性。AKBridge 会把这些问题分离到 `provider_success`、`upstream_transport`、`upstream_response`、`upstream_timeout` 和 `akshare_runtime` 范围，而不会把它们写成 MCP Schema 失败。

网络探测可自动重试，但不应自动篡改接口参数、伪造空数据或覆盖验收基线。凭据经环境变量提供，报告与结构化日志会对 token、密码、Cookie 和 API Key 脱敏。

## GitHub Actions

所有计划任务均在 GitHub 云端运行，不会在用户本机创建定时任务或常驻进程。定时工作流使用 UTC Cron，以下时间均按北京时间（UTC+8）列出；Dependabot 直接使用 `Asia/Shanghai` 时区。

| 任务 | 触发方式 | 北京时间 | 仓库行为 |
| --- | --- | --- | --- |
| 离线测试、逐接口验收、严格 MCP 门禁和构建 | 推送或 PR；每周一 | 推送或 PR 时；每周一 12:00 | 上传 Actions 报告 |
| AKShare 依赖检查 | Dependabot 每天检查 | 每天 12:00 | 更新 `pyproject.toml` 和 `uv.lock`，创建 PR |
| AKShare 受控自动合并 | 对应 PR 的完整验收成功后 | 无固定时间 | 同一主版本更新由 `github-actions[bot]` squash 合并 |
| 真实数据源全量验收 | 每月 1 日 | 12:00 | 提交状态图、验收汇总和逐接口明细 |

`.github/workflows/akbridge-maintenance.yml` 每周一运行离线测试和严格门禁，并上传 `artifacts/maintenance/`。`.github/workflows/akbridge-provider-probe.yml` 每月运行一次全量隔离数据源探测，保留全部结果但只把 MCP/Schema 回归作为严格失败条件；完成后自动刷新并提交 README 使用的状态图、验收汇总和逐接口明细。`.github/workflows/akbridge-dependabot-automerge.yml` 在维护流水线成功后核验 Dependabot PR：作者必须是 `dependabot[bot]`，只能修改 `pyproject.toml` 与 `uv.lock`，`pyproject.toml` 中只能改变 AKShare 固定版本，并且升级不能跨主版本。核验通过后由 `github-actions[bot]` squash 合并；否则保留 PR 并给出失败原因。三个流程都不使用 LLM 或人工交互。

仓库需要在 `Settings → Actions → General → Workflow permissions` 中启用 `Read and write permissions`。建议为默认分支要求 `AKBridge automated validation and maintenance / offline-contract` 状态检查；如分支规则还要求人工批准或禁止 `github-actions[bot]` 合并，自动合并工作流不会绕过规则，PR 将保持打开状态并在 Actions 中记录失败原因。
