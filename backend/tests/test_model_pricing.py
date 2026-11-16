"""模型定价的折算口径：上游美元价 → 平台点数。

建议价是运营填价时的起点，算法错了会让整张表都错一个数量级，所以把
系数、取整和缺字段的兜底都钉在测试里。
"""
import unittest

from app.services.model_pricing import (
    POINTS_PER_USD_MILLION,
    suggest_prices,
    usd_to_points_per_million,
)


class UsdToPointsTest(unittest.TestCase):
    def test_coefficient_is_documented_as_twenty_one_thousand_six_hundred(self):
        # 1 点 ≈ ¥0.001，点价 = 上游成本 × 3，汇率 7.2 ⇒ 21600 点 / 美元·百万
        self.assertEqual(float(POINTS_PER_USD_MILLION), 21600.0)

    def test_rounds_up_to_ten_points(self):
        # 0.15 × 21600 = 3240；0.6 × 21600 = 12960
        self.assertEqual(usd_to_points_per_million(0.15), 3240)
        self.assertEqual(usd_to_points_per_million(0.6), 12960)
        # 0.02 × 21600 = 432，已经是 10 的倍数；0.021 × 21600 = 453.6 → 460
        self.assertEqual(usd_to_points_per_million(0.021), 460)

    def test_missing_or_non_positive_price_is_not_guessed(self):
        self.assertIsNone(usd_to_points_per_million(None))
        self.assertIsNone(usd_to_points_per_million(0))
        self.assertIsNone(usd_to_points_per_million(-1))

    def test_tiny_price_still_gets_a_floor(self):
        self.assertEqual(usd_to_points_per_million(0.0001), 10)


class SuggestPricesTest(unittest.TestCase):
    def test_deepseek_flash_like_prices(self):
        prices = suggest_prices(0.15, 0.6)
        self.assertEqual(
            prices,
            {
                # 起扣按一轮 6k token 系统提示折算：3240 × 0.006 = 19.44 → 20
                "points_per_call": 20,
                "points_per_million_input_tokens": 3240,
                "points_per_million_output_tokens": 12960,
            },
        )

    def test_missing_output_assumes_three_times_input(self):
        prices = suggest_prices(0.5, None)
        self.assertEqual(prices["points_per_million_input_tokens"], 10800)
        self.assertEqual(prices["points_per_million_output_tokens"], 32400)

    def test_missing_input_reuses_output(self):
        prices = suggest_prices(None, 0.6)
        self.assertEqual(prices["points_per_million_input_tokens"], 12960)

    def test_no_upstream_price_means_no_suggestion(self):
        self.assertIsNone(suggest_prices(None, None))
        self.assertIsNone(suggest_prices(0, 0))


if __name__ == "__main__":
    unittest.main()
