"""波段研究前复权数据导出契约。"""

from types import SimpleNamespace

from scripts.fetch_swing_front import fetch_front_history


def test_fetch_uses_baostock_front_adjustment():
    class Result:
        error_code = "0"
        error_msg = ""
        fields = ["date", "open", "high", "low", "close", "volume", "amount"]

        def __init__(self):
            self.rows = [["2026-11-05", "30.8", "31.3", "30.56", "31.05", "100", "3000"]]

        def next(self):
            return bool(self.rows)

        def get_row_data(self):
            return self.rows.pop(0)

    calls = []

    def query(*args, **kwargs):
        calls.append((args, kwargs))
        return Result()

    frame = fetch_front_history(SimpleNamespace(query_history_k_data_plus=query), "2020-01-01", "2026-11-05")
    assert calls[0][1]["adjustflag"] == "2"
    assert frame.iloc[0]["code"] == "600887"
    assert frame.iloc[0]["close"] == 31.05
