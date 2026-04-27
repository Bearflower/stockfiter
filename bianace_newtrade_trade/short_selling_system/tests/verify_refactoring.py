#!/usr/bin/env python3
"""核心模块功能验证测试"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_imports():
    """测试1: 导入核心模块"""
    print('\n【测试1】导入核心模块...')
    try:
        from core import (
            ScoringEngine, scoring_engine,
            PatternRecognition, pattern_recognition,
            SignalManager, signal_manager,
            NewListingDetector, listing_detector,
            MonitoringScheduler, monitoring_scheduler
        )
        print('✅ 所有核心模块导入成功')
        return True
    except Exception as e:
        print(f'❌ 导入失败: {e}')
        return False

def test_scoring_engine():
    """测试2: 评分引擎功能"""
    print('\n【测试2】评分引擎功能...')
    try:
        from core import scoring_engine
        result = scoring_engine.score(
            symbol='TESTUSDT',
            oi_usd=50_000_000,
            total_volume_usd=100_000_000,
            funding_rate=0.001,
            three_tops_detected=True,
            three_tops_score=8.0,
            long_upper_shadow=True,
            long_upper_shadow_score=7.0,
            volume_divergence=False,
            volume_divergence_score=0.0,
            listing_hours=24,
            current_price=100.0
        )
        print(f'✅ 评分引擎工作正常，总分: {result.total_score:.2f}')
        print(f'   - 合约评分: {result.contract_score:.2f}')
        print(f'   - 技术评分: {result.technical_score:.2f}')
        print(f'   - 是否否决: {result.veto}')
        return True
    except Exception as e:
        print(f'❌ 评分引擎测试失败: {e}')
        import traceback
        traceback.print_exc()
        return False

def test_pattern_recognition():
    """测试3: 形态识别功能"""
    print('\n【测试3】形态识别功能...')
    try:
        from core import pattern_recognition
        from decimal import Decimal
        klines = [
            {'open': Decimal('100'), 'high': Decimal('102'), 'low': Decimal('98'), 'close': Decimal('101'), 'volume': Decimal('1000')},
            {'open': Decimal('101'), 'high': Decimal('110'), 'low': Decimal('100'), 'close': Decimal('102'), 'volume': Decimal('1500')},
            {'open': Decimal('102'), 'high': Decimal('111'), 'low': Decimal('101'), 'close': Decimal('103'), 'volume': Decimal('1200')},
            {'open': Decimal('103'), 'high': Decimal('112'), 'low': Decimal('102'), 'close': Decimal('104'), 'volume': Decimal('1100')},
            {'open': Decimal('104'), 'high': Decimal('113'), 'low': Decimal('103'), 'close': Decimal('105'), 'volume': Decimal('900')},
        ]
        detected, score, price = pattern_recognition.detect_three_tops(klines)
        print(f'✅ 形态识别工作正常')
        print(f'   - 三次冲顶检测: {detected}, 评分: {score}')
        return True
    except Exception as e:
        print(f'❌ 形态识别测试失败: {e}')
        import traceback
        traceback.print_exc()
        return False

def test_calculator():
    """测试4: 计算器功能"""
    print('\n【测试4】计算器功能...')
    try:
        from core.calculator import calculate_oi_ratio, score_oi_ratio, score_funding_rate
        oi_ratio, is_valid = calculate_oi_ratio(open_interest=50_000_000, circulating_market_cap=100_000_000)
        oi_score, veto = score_oi_ratio(oi_ratio)
        funding_score = score_funding_rate(0.001)
        print(f'✅ 计算器工作正常')
        print(f'   - OI/市值比率: {oi_ratio:.4f}, 评分: {oi_score:.2f}, 否决: {veto}')
        print(f'   - 资金费率评分: {funding_score:.2f}')
        return True
    except Exception as e:
        print(f'❌ 计算器测试失败: {e}')
        return False

def main():
    print('=' * 60)
    print('核心模块功能验证测试')
    print('=' * 60)
    
    results = []
    results.append(('导入核心模块', test_imports()))
    results.append(('评分引擎功能', test_scoring_engine()))
    results.append(('形态识别功能', test_pattern_recognition()))
    results.append(('计算器功能', test_calculator()))
    
    print('\n' + '=' * 60)
    print('测试结果汇总')
    print('=' * 60)
    
    passed = sum(1 for _, r in results if r)
    total = len(results)
    
    for name, result in results:
        status = '✅ 通过' if result else '❌ 失败'
        print(f'{name}: {status}')
    
    print(f'\n总计: {passed}/{total} 通过')
    print('=' * 60)
    
    return passed == total

if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
