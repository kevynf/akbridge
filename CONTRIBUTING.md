# 贡献指南

感谢参与 AKBridge。代码、文档和测试修改都应保持可复现，并且不能依赖人工或 LLM 才能验收。

## 开发环境

```powershell
uv sync --extra dev
```

## 提交前检查

```powershell
uv run --no-sync python -m pytest -q
uvx --from ruff ruff check src tests
uvx --from ruff ruff format --check src tests
uv run --no-sync akbridge-accept run --offline --workers 4
uv run --no-sync akbridge-maintain ci --strict --check-latest
uv build
```

离线验收不会请求第三方数据源，也不会调用 LLM。真实数据源探测由独立的定时工作流负责。

## 变更要求

- 保持 `--mode all` 的兼容性，并同步更新路由模式和语义目录。
- 新增或修改接口契约时，重新生成 `artifacts/acceptance/manifest.json` 和 `artifacts/catalog.json`。
- 不要提交凭据、Cookie、API Key、真实数据快照或本地环境目录。
- 文档正文可使用中文，但文件名使用英文主题名和语言后缀，例如 `automated-validation-and-maintenance.zh-CN.md`。
