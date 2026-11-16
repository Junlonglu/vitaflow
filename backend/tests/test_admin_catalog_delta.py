"""服务商随附的 models delta 必须做 snake_case → camelCase 翻译。

这条曾是真实事故：`context_window`/`max_tokens` 只在单条模型接口翻译过，
随服务商一起提交的 patches 没走同一套映射，保存后上下文与最大输出被静默丢掉。
"""

from app.api.admin.ai_configs import CatalogModelDelta


def test_delta_translates_model_metadata_to_catalog_case():
    delta = CatalogModelDelta(
        added=["m-new"],
        removed=["m-old"],
        patches=[
            {
                "id": "m-1",
                "data": {
                    "name": "DeepSeek Chat",
                    "input": ["text"],
                    "context_window": 128000,
                    "max_tokens": 8192,
                    "reasoning_effort": "high",
                    "reasoning": True,
                },
            }
        ],
    )

    body = delta.to_catalog_body()
    assert body["patches"][0]["data"] == {
        "name": "DeepSeek Chat",
        "input": ["text"],
        "contextWindow": 128000,
        "maxTokens": 8192,
        "reasoningEffort": "high",
        "reasoning": True,
    }


def test_delta_drops_unset_fields_so_auto_is_the_only_way_to_clear_effort():
    delta = CatalogModelDelta(patches=[{"id": "m-1", "data": {"name": "x"}}])
    assert delta.to_catalog_body()["patches"][0]["data"] == {"name": "x"}
