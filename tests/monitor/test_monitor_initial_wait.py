"""真实 bash 检查路径：只暂缓初扫等待窗口内的无记录告警。"""
from pathlib import Path
import re
import shlex
import subprocess

import pytest

TEXT = (Path(__file__).resolve().parents[2] / 'scripts/monitor.sh').read_text()


def function(name):
    match = re.search(rf'^{name}\(\) \{{\n.*?^\}}\n', TEXT, re.M | re.S)
    return match.group() if match else ''


def run_check(kind, now, *, target='2026-11-04', schedule='23:15', timeout='1800', result='missing'):
    if kind == 'scan':
        rows = {'missing': '0', 'present': '1', 'error': ''}[result]
        check = 'check_obpc_scan_missing'
    else:
        rows = {
            'missing': f'{target}|0|1|目标交易日 {target} 无 scan_runs 运行记录|1',
            'metrics': f'{target}|0|1|指标缺失',
            'leak': f'{target}|1|0|真实穿透',
            'error': 'query_failed|DatabaseError',
            'present': f'{target}|0|0|正常',
        }[result]
        rows = '__OBPC_MONITOR_RESULT__|' + rows
        check = 'check_obpc_extreme_weak_leak'
    variables = {'OBPC_TARGET_DATE': target, 'OBPC_TARGET_DEGRADED': '0',
                 'SCHEDULE_SCAN': schedule, 'KLINE_WAIT_TIMEOUT': timeout,
                 'LAST_OBPC_SCAN_MISSING': 'old_alert',
                 'LAST_OBPC_EXTREME_WEAK_BLOCK': 'old_alert', 'WEBHOOK': 'test'}
    script = '\n'.join(f'{k}={shlex.quote(v)}' for k, v in variables.items())
    script += f'''
date() {{ printf '%s\\n' {shlex.quote(now)}; }}
docker() {{ printf '%s\\n' {shlex.quote(rows)}; }}
extract_monitor_results() {{ sed -n 's/^__OBPC_MONITOR_RESULT__|//p'; }}
log() {{ :; }}
send_feishu() {{ printf 'NOTICE:%s\\n' "$2"; }}
{function('is_obpc_initial_wait_window')}
{function(check)}
{check}
printf 'STATE:%s:%s\\n' "$LAST_OBPC_SCAN_MISSING" "$LAST_OBPC_EXTREME_WEAK_BLOCK"
'''
    proc = subprocess.run(['bash', '-u', '-c', script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


@pytest.mark.parametrize('kind', ['scan', 'extreme'])
@pytest.mark.parametrize('now', ['2026-11-04 23:15:00', '2026-11-04 23:17:33', '2026-11-04 23:44:59'])
def test_missing_waits_without_clearing_existing_alert(kind, now):
    output = run_check(kind, now)
    assert 'NOTICE:' not in output
    assert 'STATE:old_alert:old_alert' in output


@pytest.mark.parametrize('kind', ['scan', 'extreme'])
@pytest.mark.parametrize('now', ['2026-11-04 23:14:59', '2026-11-04 23:45:00', '2026-11-05 08:00:00'])
def test_missing_outside_window_alerts(kind, now):
    assert 'NOTICE:' in run_check(kind, now)


@pytest.mark.parametrize('kind', ['scan', 'extreme'])
def test_custom_schedule_and_window_cross_midnight(kind):
    args = dict(schedule='23:50', timeout='1800')
    assert 'NOTICE:' not in run_check(kind, '2026-11-05 00:05:00', **args)
    assert 'NOTICE:' in run_check(kind, '2026-11-05 00:20:00', **args)


@pytest.mark.parametrize('kind', ['scan', 'extreme'])
@pytest.mark.parametrize('options', [{'schedule': ''}, {'schedule': '7:15'}, {'schedule': '24:00'}, {'timeout': ''}, {'schedule': 'invalid'}, {'timeout': 'invalid'}, {'timeout': '-1'}, {'timeout': '0'}, {'target': '2026-11-03'}, {'target': ''}])
def test_invalid_or_other_target_never_suppresses(kind, options):
    assert 'NOTICE:' in run_check(kind, '2026-11-04 23:17:33', **options)


@pytest.mark.parametrize('kind', ['scan', 'extreme'])
def test_query_failure_still_alerts_in_window(kind):
    assert 'NOTICE:' in run_check(kind, '2026-11-04 23:17:33', result='error')


@pytest.mark.parametrize('result', ['metrics', 'leak'])
def test_existing_bad_record_still_alerts_in_window(result):
    assert 'NOTICE:' in run_check('extreme', '2026-11-04 23:17:33', result=result)


def test_target_switch_uses_custom_schedule():
    """目标日切换与等待窗口使用同一个自定义调度参数。"""
    start = TEXT.index('OBPC_INITIAL_SCAN_TIME=')
    end = TEXT.index('# 每轮检查前', start)
    script = "SCHEDULE_SCAN=22:05\n" + TEXT[start:end]
    script += "\n" + function('resolve_obpc_target_date')
    script += """
log() { :; }
docker() { echo '2026-11-04|0|2026-11-03|0'; }
date() { if [ "$1" = '+%H%M' ]; then echo 2206; else echo '2026-11-04 22:06:00'; fi; }
resolve_obpc_target_date
printf '%s' "$OBPC_TARGET_DATE"
"""
    proc = subprocess.run(['bash', '-u', '-c', script], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == '2026-11-04'
