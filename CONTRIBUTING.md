# 贡献指南

[English](CONTRIBUTING.en.md)

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

将 `pyproject.toml` 与 `src/akbridge/__init__.py` 中的版本同步更新后，创建同版本标签
（例如 `v0.1.0`）并发布 GitHub Release。工作流会验证标签、运行测试和离线验收、检查
wheel/sdist 元数据，再通过 OIDC 发布到 PyPI。
