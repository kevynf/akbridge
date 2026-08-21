# 贡献指南

[English](CONTRIBUTING.en.md)

感谢参与 AKBridge。代码、文档和测试修改都应保持可复现，并且不能依赖人工或 LLM 才能验收。

## 开发环境

```powershell
uv sync --group dev
```

## 提交前检查

```powershell
uv run --no-sync python -m pytest -q
uv run --no-sync ruff check src tests
uv run --no-sync ruff format --check src tests
uv run --no-sync akbridge-accept run --offline --workers 4
uv run --no-sync akbridge-maintain ci --strict --check-latest
uv build
uv sync --extra release
uv run --no-sync twine check dist/*
```

离线验收不会请求第三方数据源，也不会调用 LLM。真实数据源探测由独立的定时工作流负责。

## 变更要求

- 保持 `--mode all` 的兼容性，并同步更新路由模式和语义目录。
- 新增或修改接口契约时，重新生成 `artifacts/acceptance/manifest.json` 和 `artifacts/catalog.json`。
- 不要提交凭据、Cookie、API Key、真实数据快照或本地环境目录。
- 文档正文可使用中文，但文件名使用英文主题名和语言后缀，例如 `automated-validation-and-maintenance.zh-CN.md`。

## 发布到 PyPI

发布工作流使用 PyPI Trusted Publishing，不在仓库中保存 API Token。首次发布前，在 PyPI
为项目 `akbridge` 配置 GitHub Publisher：仓库 `kevynf/akbridge`、工作流
`publish-pypi.yml`、环境 `pypi`。

发布由版本号驱动。同步更新 `pyproject.toml`、`src/akbridge/__init__.py`，以及 `server.json`
中的顶层版本和包版本，然后合并到默认分支。完整 CI 成功后，`auto-release.yml` 会在默认分支
仍指向已验证提交时创建同版本标签和 GitHub Release（例如 `v0.1.3`），再复用
`publish-pypi.yml` 发布 PyPI 和 MCP Registry。已有同版本 Release 时流程保持幂等；标签存在
但 Release 缺失或指向其他提交时会失败，需要维护者检查。人工发布 GitHub Release 仍会直接
触发同一发布流程。
