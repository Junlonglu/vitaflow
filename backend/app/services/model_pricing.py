"""上游 token 价 → 平台点数的折算口径（管理后台「模型定价」的建议价）。

平台点数不是独立货币，它只是上游成本的一种表达：先把美元价按汇率折成人民币，
乘一个覆盖系统提示、工具往返、失败重试与毛利的加成，再除以「一点值多少钱」。

   1 点 ≈ ¥0.001（1000 点 ≈ ¥1）
   点价 = 上游 token 成本 × 3
   ⇒ 点数/百万 = 美元/百万 × 7.2 × 3 ÷ 0.001 = 美元/百万 × 21600

于是上游 $0.15/百万的模型是 3240 点/百万：一次 2000 输入 + 500 输出 的对话
扣 6.5 + 6.5 ≈ 13 点，上游真实成本约 ¥0.011，平台收 ¥0.013。

管理后台只把这里的结果当作「建议值」展示，运营可以逐格改写；真正的计费
仍然读 ``ai_model_configs`` 的三个价格字段。
"""

from __future__ import annotations

from decimal import ROUND_CEILING, Decimal

# 汇率与加成集中在这里：调整售卖策略只改这两个常量，建议价与文档一起动。
FX_CNY_PER_USD = Decimal("7.2")
PRICE_MARKUP = Decimal("3")
POINT_UNIT_CNY = Decimal("0.001")

POINTS_PER_USD_MILLION = FX_CNY_PER_USD * PRICE_MARKUP / POINT_UNIT_CNY  # 21600

# 起扣价按「一轮系统提示的 token 成本」估：pi 的系统提示 + 工具描述约 6k token，
# 再向上取整到 10 点。短问题因此不会因为 token 少而白送一次模型调用。
SYSTEM_PROMPT_TOKENS = Decimal("6000")


def _round_up_to_ten(value: Decimal) -> int:
    return int((value / 10).to_integral_value(rounding=ROUND_CEILING) * 10)


def usd_to_points_per_million(usd: float | None) -> int | None:
    """美元/百万 token → 点/百万 token；缺失或非正数返回 None（不猜）。"""
    if usd is None:
        return None
    value = Decimal(str(usd))
    if value <= 0:
        return None
    return max(10, _round_up_to_ten(value * POINTS_PER_USD_MILLION))


def suggest_prices(price_input: float | None, price_output: float | None) -> dict[str, int] | None:
    """按上游价算一组建议定价；上游没有任何价格时不建议（让运营自己填）。"""
    input_rate = usd_to_points_per_million(price_input)
    output_rate = usd_to_points_per_million(price_output)
    if input_rate is None and output_rate is None:
        return None
    if input_rate is None:
        input_rate = int(output_rate or 0)
    if output_rate is None:
        # 输出普遍是输入的 3 倍左右，缺一侧时按这个比例补，避免出现「输出免费」。
        output_rate = input_rate * 3
    per_call = _round_up_to_ten(Decimal(input_rate) * SYSTEM_PROMPT_TOKENS / Decimal(1000000))
    return {
        "points_per_call": max(10, per_call),
        "points_per_million_input_tokens": input_rate,
        "points_per_million_output_tokens": output_rate,
    }
