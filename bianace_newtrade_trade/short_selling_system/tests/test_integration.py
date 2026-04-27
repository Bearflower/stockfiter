#!/usr/bin/env python3
"""集成测试：主程序初始化"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def main():
    print('=' * 60)
    print('集成测试：主程序初始化')
    print('=' * 60)
    
    # 测试主程序导入
    print('\n【测试1】导入主程序模块...')
    try:
        import main
        print('✅ 主程序模块导入成功')
    except Exception as e:
        print(f'❌ 主程序模块导入失败: {e}')
        import traceback
        traceback.print_exc()
        return False
    
    # 测试系统初始化
    print('\n【测试2】系统组件初始化...')
    try:
        detector, scoring_engine, pattern_recognition, signal_manager, scheduler = main.init_system()
        print('✅ 系统组件初始化成功')
        print(f'   - 新币检测器: {type(detector).__name__}')
        print(f'   - 评分引擎: {type(scoring_engine).__name__}')
        print(f'   - 形态识别: {type(pattern_recognition).__name__}')
        print(f'   - 信号管理器: {type(signal_manager).__name__}')
        print(f'   - 调度器: {type(scheduler).__name__}')
    except Exception as e:
        print(f'❌ 系统组件初始化失败: {e}')
        import traceback
        traceback.print_exc()
        return False
    
    print('\n' + '=' * 60)
    print('✅ 集成测试通过')
    print('=' * 60)
    return True

if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
