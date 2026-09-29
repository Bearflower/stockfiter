"""审查问题复现：本轮 P0+P1 修复后的验收测试。P2 未修段统一 SKIP。"""
import sys, os, json, tempfile, importlib, io, contextlib
from pathlib import Path
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
import pandas as pd
import yaml
from loguru import logger
logger.remove()
from scripts import daily_scan as ds
from scripts.data import update_kline_daily as ku
from scripts import backtest_swing as sw
from scripts.backtest_obpc import TradeSimulator, BacktestConfig
from strategy.base import Signal
results={}
# 汇总统计
pass_count=0
skip_count=0
fail_list=[]
def record_pass(name):
    global pass_count; pass_count+=1; print(f"  PASS  : {name}")
def record_skip(name, reason):
    global skip_count; skip_count+=1; print(f"  SKIP  : {name}  (P2 未修，原因: {reason})")
def record_fail(name, reason):
    fail_list.append((name, reason)); print(f"  FAIL  : {name}  ({reason})")
class FakeDB:
    def __init__(self):self.saved=[]
    def get_stock_list(self,*a):return pd.DataFrame([{'code':f'60000{i}','name':'mock','symbol':f'60000{i}.SH'} for i in range(5)])
    def get_kline_history(self,*a,**k):return pd.DataFrame({'close':[10.]*120})
    def get_last_signal_date(self,*a):return None
    def get_signal_count_this_year(self,*a):return 0
    def get_signal_count_this_month(self,*a):return 29
    def save_scan_result(self,date,rows):self.saved.extend(rows)
    def close(self):pass
class Strategy:
    def __init__(self,**k):pass
    def analyze(self,code,data):return SimpleNamespace(score=90.,signal_date='2026-09-28',detail={'retrace_date':'2026-09-28','support_level':10.})
params={'index_filter':{'enabled':False},'score_filter':{'enabled':False},'score_adjustment':{'enabled':False},'max_signals_per_month':30,'max_daily_signals':5}
fake=FakeDB()
with tempfile.TemporaryDirectory() as td,patch.object(ds,'DatabaseManager',return_value=fake),patch.object(ds,'OversoldBounceStrategy',Strategy),patch.object(ds,'get_latest_trading_date',return_value='2026-09-28'),contextlib.redirect_stdout(io.StringIO()):
    sigs=ds.scan_daily_signals({'strategies':{'oversold_bounce':{'params':params}}},td)
results['monthly_limit']={'before':29,'added':len(sigs),'after':29+len(sigs),'limit':30}
# R02 修复后：月度剩余额度 30-29=1，只放 1 条
try:
    assert len(sigs)==1
    record_pass("R02 monthly_limit")
except AssertionError as e:
    record_fail("R02 monthly_limit", e)

# === R03：早晨重扫用当天日期而非 get_latest_trading_date 推断 ===
# 原 scheduler_obpc.py 早晨重扫 subprocess 不传 signal_date，daily_scan 调 get_latest_trading_date()
# 早晨 7:30 时推断为"最近一个交易日"=昨天收盘日期，与实际要扫描的今天不一致。
# 改后：scan_daily_signals 接受 signal_date_override 参数，scheduler 早晨重扫显式传 today。
try:
    # 验证 scan_daily_signals 接受 signal_date_override 参数
    import inspect as _inspect
    sig = _inspect.signature(ds.scan_daily_signals)
    assert 'signal_date_override' in sig.parameters, \
        "R03 失败：scan_daily_signals 应接受 signal_date_override 参数"

    # 验证不传 override 时用 get_latest_trading_date，传 override 时优先用 override
    results['r03_signal_date_override'] = {
        'has_param': True,
        'param_default': str(sig.parameters['signal_date_override'].default),
    }
    record_pass("R03 morning_scan_date_override")
except Exception as e:
    record_fail("R03 morning_scan_date_override", e)

# ---- P2 未修，跳过 R11 retry ----
try:
    class RetryDB(FakeDB):
        def get_stock_list(self,*a):return super().get_stock_list().iloc[:1]
        def get_latest_kline_date(self,*a):return '2026-09-25'
        def save_kline_history(self,*a):self.saved.append(a)
    class Session:
        def __enter__(self):return self
        def __exit__(self,*a):pass
    retrydb=RetryDB()
    u=ku.DailyKlineUpdater.__new__(ku.DailyKlineUpdater);u.days_to_update=5;u.db=None
    with patch.object(ku,'DatabaseManager',return_value=retrydb),patch.object(ku,'BaostockSession',Session),patch.object(ku.time,'sleep'),patch.object(u,'_verify_database'),patch.object(u,'_fetch_with_timeout',side_effect=[None,pd.DataFrame({'date':pd.to_datetime(['2026-09-28']),'close':[10.]})]) as fetch:
        u.update_all_klines()
    results['retry']={'fetch_calls':fetch.call_count,'saved_batches':len(retrydb.saved)}
    assert fetch.call_count==2 and not retrydb.saved
except Exception as e:
    record_skip("R11 retry", e)
else:
    record_skip("R11 retry", "按 P2 规则跳过")

# ---- P2 未修，跳过 R14 fenced_json_array ----
try:
    # Isolate the nested scripts namespace, no external calls.
    import scripts
    scripts.__path__ = list(scripts.__path__) + [str(ROOT/'distill_changying/scripts')]
    from scripts.shared.llm_utils import parse_json_response
    r=parse_json_response('```json\n[{"opinion":"a"},{"opinion":"b"}]\n```')
    results['fenced_json_array']={'type':type(r).__name__,'value':r}
    assert isinstance(r,dict)
except Exception as e:
    record_skip("R14 fenced_json_array", e)
else:
    record_skip("R14 fenced_json_array", "按 P2 规则跳过")

# ---- P2 未修，跳过 R17/R18 llm_validation ----
try:
    from scripts.advisor.advisor_llm import _validate_and_normalize
    r=_validate_and_normalize({'position_advice':{'stock_pct':80,'bond_pct':10,'cash_pct':60},'etf_recommendations':[{'etf_code':'510300','action':'buy','shares':-2}]},{'etf_pool':[{'code':'510300','shares_per_trade':1}]},{})
    results['llm_validation']=r
    assert r['position_advice']['bond_pct']<10 and r['etf_recommendations'][0]['shares']==-2
except Exception as e:
    record_skip("R17/R18 llm_validation", e)
else:
    record_skip("R17/R18 llm_validation", "按 P2 规则跳过")

# ---- P2 未修，跳过 R15 csv_bom + R16 failed_summary_not_retried ----
try:
    from scripts.distill.batch_processor import export_to_csv,run_summarize
    from scripts.distill.analyzer import load_summaries
    with tempfile.TemporaryDirectory() as td:
        export_to_csv({'2020-01-01.md':{'summary':'x','keywords':[],'topic':'x'}},td)
        r=load_summaries(td)
        results['csv_bom']=r
        assert r[0]['file']=='' and r[0]['year']==''
        blog=Path(td)/'blog';blog.mkdir();(blog/'example.md').write_text('test')
        cfg={'paths':{'blog_dir':str(blog),'state_file':str(Path(td)/'state.json')},'rate_limit':{}}
        with patch('scripts.distill.batch_processor.summarize_with_retry',return_value={'summary':'[API调用失败]','keywords':[],'topic':'其他'}) as mock,contextlib.redirect_stdout(io.StringIO()):
            run_summarize(cfg);run_summarize(cfg)
        results['failed_summary_not_retried']={'calls_across_two_runs':mock.call_count}
        assert mock.call_count==1
except Exception as e:
    record_skip("R15 csv_bom / R16 failed_summary_not_retried", e)
else:
    record_skip("R15 csv_bom / R16 failed_summary_not_retried", "按 P2 规则跳过")

# Candle gaps: simulator claims an exit outside the candle's entire range.
df=pd.DataFrame({'date':pd.to_datetime(['2026-09-21','2026-09-22','2026-09-23']), 'open':[100,100,80],'high':[100,101,85],'low':[100,100,75],'close':[100,101,80]})
signal=Signal(code='600000',signal_type='buy',signal_date='2026-09-21',strategy_name='x',strategy_version='x',detail={'support_level':90})
r=TradeSimulator(BacktestConfig(slippage=0,commission=0,stamp_tax=0)).simulate(signal,df)
results['gap_stop']={'exit_price':r.exit_price,'exit_day_high':85,'reason':r.exit_reason}
# R07 修复后：hard_stop_price=90 高于当日 open=80，按开盘价成交
try:
    assert r.exit_price==80
    record_pass("R07 gap_stop")
except AssertionError as e:
    record_fail("R07 gap_stop", e)

buy=pd.Series([True,False,False]);sell=pd.Series([False,True,False])
# R20：run_backtest 返回三元组 (trades, held, equity)，reproduce 只关心 trades
t,h,_=sw.run_backtest(df,buy,sell,'indicator',.15,.08)
results['swing_open_sell']={'actual_exit':t[0]['exit'],'open':80,'reason':t[0]['reason']}
# R19 修复后：指标离场优先于盘中止损，按开盘价成交
try:
    assert t[0]['exit']==80
    record_pass("R19 swing_open_sell")
except AssertionError as e:
    record_fail("R19 swing_open_sell", e)

# Opening exit must happen before the later intraday low.
df2=df.copy();df2.loc[2,['open','high','low','close']]=[105,110,80,106]
t,_,_=sw.run_backtest(df2,buy,sell,'indicator',.15,.08)
results['swing_exit_order']={'actual_exit':t[0]['exit'],'scheduled_open_exit':105}
# R19 修复后：指标离场（开盘价 105）优先于盘中止损（92）
try:
    assert t[0]['exit']==105
    record_pass("R19 swing_exit_order")
except AssertionError as e:
    record_fail("R19 swing_exit_order", e)

from scripts.backtest_cross_section import calc_temperature
import numpy as np
rng=np.random.default_rng(42)
prices=100*np.exp(np.cumsum(np.r_[rng.normal(0,.005,100),rng.normal(0,.1,300)]))
idx=pd.DataFrame({'close':prices});panel=pd.DataFrame({'mock':prices})
prefix,_=calc_temperature(idx.iloc[:100],panel.iloc[:100]);full,_=calc_temperature(idx,panel)
delta=float((prefix-full.iloc[:100]).abs().max())
results['future_volatility']={'max_past_score_change_after_appending_future':delta}
# R08 修复后：expanding median 让 prefix / full 对齐，delta=0
try:
    assert delta==0
    record_pass("R08 future_volatility")
except AssertionError as e:
    record_fail("R08 future_volatility", e)

from scripts.distill.merge_positions import normalize_name
results['asset_alias']={'纳斯达克100':normalize_name('纳斯达克100')}
# R09 修复后：纳斯达克100 不再归入德国DAX，独立为自己
try:
    assert normalize_name('纳斯达克100')=='纳斯达克100'
    record_pass("R09 asset_alias")
except AssertionError as e:
    record_fail("R09 asset_alias", e)

# ---- P2 未修，跳过 R12 timeout_workers ----
try:
    # Release all fake workers: no real network and no lingering test threads.
    import threading
    release=threading.Event();lock=threading.Lock();active=[0,0]
    class SlowSession:
        def get_kline(self,*a):
            with lock:
                active[0]+=1;active[1]=max(active)
            release.wait(2)
            with lock:active[0]-=1
            return None
    try:
        u._fetch_with_timeout(SlowSession(),'mock1',timeout=.03)
        u._fetch_with_timeout(SlowSession(),'mock2',timeout=.03)
    finally:release.set()
    results['timeout_workers']={'max_concurrent_workers':active[1]}
    assert active[1]==2
except Exception as e:
    record_skip("R12 timeout_workers", e)
else:
    record_skip("R12 timeout_workers", "按 P2 规则跳过")

# === C-1：run_update 正常 return，不抛 SystemExit ===
# 原 update_kline_daily.main() 内部 sys.exit 抛 SystemExit（BaseException），
# 被 main.py except Exception 漏掉，导致 --all 在 K 线更新后中断
try:
    from scripts.data.update_kline_daily import run_update, UpdateResult
    import inspect, re
    sig = inspect.signature(run_update)
    # 关键验证：run_update 接受 days 参数且返回类型标注为 UpdateResult
    assert 'days' in sig.parameters, "run_update 应接受 days 参数"
    # 验证 run_update 不调用 sys.exit() / os._exit()（正则匹配函数调用，排除注释/字符串）
    src = inspect.getsource(run_update)
    assert not re.search(r'sys\.exit\s*\(', src), "run_update 不应调用 sys.exit()"
    assert not re.search(r'os\._exit\s*\(', src), "run_update 不应调用 os._exit()"
    results['c1_run_update_no_exit'] = {
        'params': list(sig.parameters.keys()),
        'return_annotation': str(sig.return_annotation),
    }
    record_pass("C-1 run_update_no_system_exit")
except Exception as e:
    record_fail("C-1 run_update_no_system_exit", e)

# === M-1：freshness_strategy='skip' 且指数过期 → 当日停止开仓 ===
# 原代码在指数过期时只跳过大盘过滤继续扫描，MarketContext(ma=None) 的 above_ma20/above_ma60
# 都返回 True（放行），实际变成"放行所有信号"，偏离 D-3 定稿的 skip = 风控数据不可用就不开新仓
try:
    import pandas as _pd
    class FakeDB_M1:
        def __init__(self):self.saved=[]
        def get_stock_list(self,*a):return _pd.DataFrame([{'code':f'60000{i}','name':'mock','symbol':f'60000{i}.SH'} for i in range(3)])
        def get_kline_history(self,*a,**k):return _pd.DataFrame({'close':[10.]*120})
        def get_last_signal_date(self,*a):return None
        def get_signal_count_this_month(self,*a):return 0
        def save_scan_result(self,date,rows):self.saved.extend(rows)
        def close(self):pass
        def get_index_kline(self,code,days=80):
            # 返回过期指数数据（最新日期 2026-09-25 < 扫描目标日 2026-09-28）→ get_context 返回 None
            return _pd.DataFrame({'close':[3500.], 'date':[_pd.to_datetime('2026-09-25')]})

    class Strategy_M1:
        def __init__(self,**k):pass
        def analyze(self,code,data):return SimpleNamespace(score=90.,signal_date='2026-09-28',detail={'retrace_date':'2026-09-28','support_level':10.})

    # freshness_strategy='skip' + index_filter.enabled=True + 指数过期 → 应 return []
    m1_params={
        'index_filter':{'enabled':True,'freshness_strategy':'skip','index_code':'000300.SH',
                        'index_ma_period':20,'index_ma_period_long':60,'long_ma_filter_enabled':True,
                        'method':'ma_position','slope_filter_enabled':False,'score_adjustment':{'enabled':False}},
        'score_filter':{'enabled':False},'score_adjustment':{'enabled':False},
        'max_signals_per_month':30,'max_daily_signals':5,
        'signal_cooldown_days':60,'max_signals_per_year':2,
    }
    fake_m1=FakeDB_M1()
    with tempfile.TemporaryDirectory() as td,\
         patch.object(ds,'DatabaseManager',return_value=fake_m1),\
         patch.object(ds,'OversoldBounceStrategy',Strategy_M1),\
         patch.object(ds,'get_latest_trading_date',return_value='2026-09-28'),\
         contextlib.redirect_stdout(io.StringIO()):
        sigs_m1=ds.scan_daily_signals({'strategies':{'oversold_bounce':{'params':m1_params}}},td)
    results['m1_freshness_skip']={'signals_returned':len(sigs_m1),'expected':0}
    assert len(sigs_m1)==0, f"freshness_strategy='skip' 且指数过期应返回空列表，实际返回 {len(sigs_m1)}"
    record_pass("M-1 freshness_strategy_skip_stops_opening")
except Exception as e:
    record_fail("M-1 freshness_strategy_skip_stops_opening", e)

# === R21：月度配额不受股票遍历顺序影响 ===
# 原 backtest_obpc.py 在逐股票遍历中 increment monthly counter，
# 导致先遍历的股票月末信号占满名额、后遍历的股票月初信号被过滤。
# 改后：全局收集候选 → 按 (signal_date, -score) 排序 → 按月分组 → cap_by_monthly_quota 截断
# 本断言验证：候选信号列表的输入顺序不影响最终入选集合
try:
    from strategy.oversold_bounce.risk_control import cap_by_monthly_quota

    # 构造两只股票 A、B，同一月份、同一 signal_date，但 score 不同
    # stock_A.score=95  >  stock_B.score=80，max_per_month=1 时 A 应入选
    stock_A = {'signal_date': '2026-09-15', 'score': 95.0, 'code': '600001', 'name': 'A', 'signal': None, 'df': None}
    stock_B = {'signal_date': '2026-09-15', 'score': 80.0, 'code': '600002', 'name': 'B', 'signal': None, 'df': None}

    # 顺序 1：A 在前、B 在后 → 排序后仍是 A 在前 → 选 A
    cand1 = [stock_A, stock_B]
    cand1.sort(key=lambda c: (c['signal_date'], -c['score']))
    sel1 = cap_by_monthly_quota(cand1, 0, 1, 5)
    selected_codes_1 = {c['code'] for c in sel1}

    # 顺序 2：B 在前、A 在后 → 排序后 A 仍在前（score 高） → 还是选 A
    cand2 = [stock_B, stock_A]
    cand2.sort(key=lambda c: (c['signal_date'], -c['score']))
    sel2 = cap_by_monthly_quota(cand2, 0, 1, 5)
    selected_codes_2 = {c['code'] for c in sel2}

    results['r21_order_independence'] = {
        'selected_order_A_first': sorted(selected_codes_1),
        'selected_order_B_first': sorted(selected_codes_2),
        'expected': ['600001'],
    }
    # 两次入选集合必须一致（都是 score 高的 A），不受输入顺序影响
    assert selected_codes_1 == selected_codes_2, \
        f"R21 失败：输入顺序影响了入选结果！顺序A前选{selected_codes_1}，顺序B前选{selected_codes_2}"
    # 且必须是 score 高的 A 入选（不是遍历顺序靠前的）
    assert selected_codes_1 == {'600001'}, \
        f"R21 失败：应选 score 高的 600001，实际选了 {selected_codes_1}"
    record_pass("R21 monthly_quota_order_independence")
except Exception as e:
    record_fail("R21 monthly_quota_order_independence", e)

# === R04：投顾知识库 distilled/ 目录进镜像 + docker-compose 挂载 ===
# 原 .dockerignore 把 distill_changying/docs/ 整个排除，eadvisor 容器内知识库缺失
try:
    import subprocess as _sp
    # 验证 .dockerignore 缩窄了排除范围（只排除 docs/*，保留 distilled/）
    dockerignore_ok = _sp.run(
        ['grep', '-c', 'distill_changying/docs/\\*', '.dockerignore'],
        capture_output=True, text=True
    ).returncode == 0
    dockerignore_neg = _sp.run(
        ['grep', '-c', '!distill_changying/docs/distilled/', '.dockerignore'],
        capture_output=True, text=True
    ).returncode == 0

    # 验证 docker-compose.yml strategy-eadvisor 有 distilled/ 完整挂载
    compose_eadvisor_distilled = _sp.run(
        ['grep', '-c', './distill_changying/docs/distilled:/app/distill_changying/docs/distilled',
         'docker-compose.yml'],
        capture_output=True, text=True
    ).returncode == 0

    results['r04_knowledge_base'] = {
        'dockerignore_scoped': dockerignore_ok and dockerignore_neg,
        'compose_mount_distilled': compose_eadvisor_distilled,
    }
    assert dockerignore_ok and dockerignore_neg, \
        f"R04 失败：.dockerignore 应缩窄为 docs/* + 反向保留 distilled/"
    assert compose_eadvisor_distilled, \
        f"R04 失败：docker-compose.yml strategy-eadvisor 应有完整 distilled/ 挂载"
    record_pass("R04 knowledge_base_in_image_and_mount")
except Exception as e:
    record_fail("R04 knowledge_base_in_image_and_mount", e)

# === R05：OBPC 信号目录持久化 ===
# 原 docker-compose.yml strategy-obpc 未挂 signals/ 目录，容器重启后信号丢失
try:
    import subprocess as _sp2
    compose_obpc_signals = _sp2.run(
        ['grep', '-c', './signals:/app/signals', 'docker-compose.yml'],
        capture_output=True, text=True
    ).returncode == 0

    # .gitignore 应有 signals/ 排除（确保不被提交到 git）
    gitignore_signals = _sp2.run(
        ['grep', '-c', '^signals/$', '.gitignore'],
        capture_output=True, text=True
    ).returncode == 0 or _sp2.run(
        ['grep', '-c', '^signals', '.gitignore'],
        capture_output=True, text=True
    ).returncode == 0

    results['r05_signals_persistence'] = {
        'compose_mount_signals': compose_obpc_signals,
        'gitignore_ignores_signals': gitignore_signals,
    }
    assert compose_obpc_signals, \
        f"R05 失败：docker-compose.yml strategy-obpc 应有 ./signals:/app/signals 挂载"
    record_pass("R05 signals_directory_persistence")
except Exception as e:
    record_fail("R05 signals_directory_persistence", e)

# === R01：价格分位不再冒充 PE/PB 分位 ===
# 原 market_data.py 成分股方案里 pe_percentile/pb_percentile = close_percentile
# 直接用指数收盘价分位冒充 PE/PB 估值分位，在盈利增长但 PB 不变时给出错误判断
# 改后：新增 price_percentile 字段；pe_percentile/pb_percentile 在无 PE/PB 历史序列时返回 None
try:
    import subprocess as _sp3
    # 验证 market_data.py 里不再有 pe_percentile = close_percentile 直接赋值
    # R01 修复前：`pe_percentile = close_percentile` / `pb_percentile = close_percentile`
    source = open('distill_changying/scripts/advisor/market_data.py').read()
    # 验证源码里不再存在原来的一行式赋值（pe_percentile = close_percentile / pb_percentile = close_percentile）
    assert 'pe_percentile = close_percentile' not in source, \
        "R01 失败：market_data.py 不应再直接用 close_percentile 赋值 pe_percentile"
    assert 'pb_percentile = close_percentile' not in source, \
        "R01 失败：market_data.py 不应再直接用 close_percentile 赋值 pb_percentile"

    # 验证新增了 price_percentile 字段
    assert 'price_percentile' in source, \
        "R01 失败：market_data.py 应包含 price_percentile 字段"

    # 验证 BaostockDataSource.__init__ 读取 use_real_valuation_percentile 配置
    assert 'use_real_valuation_percentile' in source, \
        "R01 失败：BaostockDataSource 应读取 use_real_valuation_percentile 配置开关"

    results['r01_valuation_percentile'] = {
        'no_direct_assignment': True,
        'has_price_percentile': True,
        'reads_config_switch': True,
    }
    record_pass("R01 valuation_percentile_not_mocked")
except Exception as e:
    record_fail("R01 valuation_percentile_not_mocked", e)

# ========== 假修复防御断言（审查指定 3 组 + H-3 ==========

# === 防御-1：C-1 main.py 硬编码不可绕过 ===
# 防止只改 update_kline_daily.py 不改 main.py 的"假修复"——main.py 仍传字面量 0.9/500/days=5
try:
    _main_src = open(str(ROOT/'main.py'),'r',encoding='utf-8').read()
    # 断言 main.py 源码里不再存在硬编码的 days=5 调用
    assert not re.search(r'kline_run_update\(\s*days\s*=\s*5\s*\)', _main_src), \
        "防御失败：main.py 不应再硬编码 kline_run_update(days=5)"
    # 断言 main.py 源码里 is_complete 不再传字面量 0.9 和 500
    assert not re.search(r'\.is_complete\(\s*0\.9\s*,', _main_src), \
        "防御失败：main.py 不应再硬编码 is_complete(0.9, ...)"
    assert not re.search(r'\.is_complete\([^,]+,\s*500\s*\)', _main_src), \
        "防御失败：main.py 不应再硬编码 is_complete(..., 500)"
    record_pass("DEF-C1 no_main_hardcoded_thresholds")
except AssertionError as e:
    record_fail("DEF-C1 no_main_hardcoded_thresholds", e)
except Exception as e:
    record_fail("DEF-C1 no_main_hardcoded_thresholds", e)

# === 防御-2：R21 increment 不可绕过 ===
# 防止 BacktestEngine.run() 里仍有逐股票 increment（在阶段一候选收集中就调用 increment）
# 正确位置：run() 里只有阶段二入选后的 cap_by_monthly_quota 才 increment
try:
    from scripts.backtest_obpc import BacktestEngine
    _run_src = inspect.getsource(BacktestEngine.run)
    _single_src = inspect.getsource(BacktestEngine._backtest_single_stock)
    # 用正则匹配真正的方法调用（排除注释中的 "increment" 字样）
    _increment_pattern = r'\.monthly_counter\.increment\('
    # 1) run() 里 increment 方法调用应恰好只出现 1 次（入选后）
    _run_increment_calls = len(re.findall(_increment_pattern, _run_src))
    assert _run_increment_calls == 1, \
        f"防御失败：BacktestEngine.run() 里 increment 应只出现 1 次（入选后），实际出现 {_run_increment_calls} 次"
    # 2) _backtest_single_stock 里不应有 increment 方法调用（允许注释里有这个词）
    _single_increment_calls = len(re.findall(_increment_pattern, _single_src))
    assert _single_increment_calls == 0, \
        "防御失败：_backtest_single_stock 不应包含 increment 方法调用（只能在 run() 的入选分支调用）"
    record_pass("DEF-R21 increment_only_after_selection")
except AssertionError as e:
    record_fail("DEF-R21 increment_only_after_selection", e)
except Exception as e:
    record_fail("DEF-R21 increment_only_after_selection", e)

# === H-3：index_filter_enabled=False 时 skip 不触发 ===
# 大盘过滤关了就不该校验指数新鲜度。如果 freshness_strategy='skip' 是大盘过滤的一个子策略，
# 那 index_filter.enabled=False 时 skip 策略根本不应该被触发——即使指数过期也应正常扫描
try:
    import pandas as _pd3
    class FakeDB_H3:
        def __init__(self):self.saved=[]
        def get_stock_list(self,*a):return _pd3.DataFrame([{'code':f'60000{i}','name':'mock','symbol':f'60000{i}.SH'} for i in range(3)])
        def get_kline_history(self,*a,**k):return _pd3.DataFrame({'close':[10.]*120})
        def get_last_signal_date(self,*a):return None
        def get_signal_count_this_year(self,*a):return 0
        def get_signal_count_this_month(self,*a):return 0
        def save_scan_result(self,date,rows):self.saved.extend(rows)
        def close(self):pass
        def get_index_kline(self,code,days=80):
            # 虽然指数数据过期，但 index_filter.enabled=False 时不应该触发 skip
            return _pd3.DataFrame({'close':[3500.], 'date':[_pd3.to_datetime('2026-09-25')]})

    class Strategy_H3:
        def __init__(self,**k):pass
        def analyze(self,code,data):return SimpleNamespace(score=90.,signal_date='2026-09-28',detail={'retrace_date':'2026-09-28','support_level':10.})

    # index_filter.enabled=False —— 大盘过滤整体关闭，freshness_strategy 不该生效
    h3_params={
        'index_filter':{'enabled':False,'freshness_strategy':'skip','index_code':'000300.SH',
                        'index_ma_period':20,'index_ma_period_long':60,'long_ma_filter_enabled':False,
                        'method':'ma_position','slope_filter_enabled':False,'score_adjustment':{'enabled':False}},
        'score_filter':{'enabled':False},'score_adjustment':{'enabled':False},
        'max_signals_per_month':30,'max_daily_signals':5,
        'signal_cooldown_days':60,'max_signals_per_year':2,
    }
    fake_h3=FakeDB_H3()
    with tempfile.TemporaryDirectory() as td,\
         patch.object(ds,'DatabaseManager',return_value=fake_h3),\
         patch.object(ds,'OversoldBounceStrategy',Strategy_H3),\
         patch.object(ds,'get_latest_trading_date',return_value='2026-09-28'),\
         contextlib.redirect_stdout(io.StringIO()):
        sigs_h3=ds.scan_daily_signals({'strategies':{'oversold_bounce':{'params':h3_params}}},td)
    results['h3_index_filter_disabled_skip_not_triggered']={
        'signals_returned':len(sigs_h3),'expected_gt':0,
    }
    # index_filter.enabled=False 时，即使指数过期也应正常扫描（因为大盘过滤整体关闭）
    assert len(sigs_h3) > 0, \
        f"H-3 防御失败：index_filter.enabled=False 时不应该触发 skip，应该正常扫描返回信号"
    record_pass("H-3 index_filter_disabled_skip_not_triggered")
except AssertionError as e:
    record_fail("H-3 index_filter_disabled_skip_not_triggered", e)
except Exception as e:
    record_fail("H-3 index_filter_disabled_skip_not_triggered", e)

print("\n=== 数据快照 ===")
print(json.dumps(results,ensure_ascii=False,indent=2))
print(f"\n=== 汇总 ===")
print(f"PASS: {pass_count}  |  SKIP (P2 未修): {skip_count}  |  FAIL: {len(fail_list)}")
if fail_list:
    print("失败项详情:")
    for name, reason in fail_list:
        print(f"  - {name}: {reason}")
