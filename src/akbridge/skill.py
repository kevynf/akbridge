"""The bundled, deterministic model-facing usage guide exposed as a resource."""

from __future__ import annotations

SKILL_URI = "akbridge://skill"

SKILL_TEXT = """# AKBridge 使用规则

1. 先调用 `akbridge_search`，用接口名称、数据主题或类别检索候选接口。
2. 对候选接口调用 `akbridge_describe`，确认必填参数、签名、返回类型和副作用标记。
3. 只使用确认过的规范名称调用 `akbridge_call`；把参数放在 `arguments` 中。
4. 大表优先使用 `output_mode=summary` 或 `compact`，再通过 `page` 和 `page_size` 翻页。
5. `side_effect=true` 的接口可能修改全局配置或提交凭据，调用前必须由上层业务明确授权。
6. 搜索结果是本地目录检索结果，不代表数据源当前可用；调用错误会原样报告。

AKBridge 的搜索、目录和验收流程不调用 LLM，也不依赖远程向量数据库，
因此可以在自动化 CI 中重复执行。
"""
