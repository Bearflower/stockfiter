# 实盘部署准备指南

## 测试网测试结果

**测试日期**: 2026-03-19  
**测试结果**: 4/5 功能通过 ✅

### 已验证功能
- ✅ 市场数据获取
- ✅ 技术指标计算
- ✅ 市场状态识别
- ✅ 飞书报警功能
- ⚠️ 币安 API 连接（需要测试网专用密钥）

## 实盘部署前检查清单

### 1. 技术准备

#### 1.1 API 密钥配置
- [ ] 申请币安测试网专用 API 密钥（用于测试）
  - 访问：https://testnet.binancefuture.com
  - 使用 GitHub 账号登录
  - 创建 API 密钥
  - 启用"读取信息"和"合约交易"权限
  
- [ ] 准备币安主网 API 密钥（用于实盘）
  - 访问：https://www.binance.com
  - 创建 API 密钥
  - 启用"读取信息"和"合约交易"权限
  - **重要**: 设置 IP 白名单
  - **建议**: 限制 API 密钥权限

#### 1.2 配置文件
- [ ] 复制配置模板
  ```bash
  cp config/config.yaml.template config/config.yaml
  ```

- [ ] 配置环境变量（`config/.env`）
  ```bash
  # 测试网
  BINANCE_API_KEY=your_testnet_api_key
  BINANCE_API_SECRET=your_testnet_api_secret
  
  # 实盘时修改
  BINANCE_API_KEY=your_live_api_key
  BINANCE_API_SECRET=your_live_api_secret
  
  # 飞书报警
  FEISHU_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/xxx
  ```

- [ ] 调整配置参数（`config/config.yaml`）
  ```yaml
  exchange:
    testnet: true  # 测试时 true，实盘改 false
    symbol: "BTCUSDT"
  
  monitoring:
    alert:
      enabled: true  # 启用报警
  ```

#### 1.3 系统环境
- [ ] Python 3.8+ 已安装
- [ ] 依赖已安装：`pip install -r requirements.txt`
- [ ] 创建必要目录：`logs/`, `data/`
- [ ] 测试日志写入正常

### 2. 策略参数确认

#### 2.1 指标参数（适合当前市场）
```yaml
strategy:
  indicators:
    adx_period: 14           # ✅ 标准参数
    adx_trend_threshold: 25  # ✅ 已验证
    adx_weak_threshold: 20   # ✅ 已验证
    ema_fast: 20             # ✅ 已验证
    ema_slow: 50             # ✅ 已验证
    atr_period: 14           # ✅ 标准参数
```

#### 2.2 网格参数
```yaml
strategy:
  grid:
    base_grid_count: 30      # ✅ 适中
    min_grid_count: 20       # ✅ 保守
    max_grid_count: 50       # ✅ 合理
    base_atr_window: 90      # ✅ 长期参考
```

#### 2.3 风险参数（重要！）
```yaml
strategy:
  risk:
    hard_stop_loss: -0.08        # ⚠️ -8%，建议值
    trailing_profit_start: 0.15  # ⚠️ 15% 启动
    trailing_profit_retrace: 0.5 # ⚠️ 50% 回撤
    emergency_break_layers: 3    # ✅ 已验证
    emergency_break_window: 300  # ✅ 5 分钟
    position_coefficient: 0.5    # ⚠️ 保守仓位
```

**风险提示**:
- 硬止损：-8% 表示最大亏损为本金的 8%
- 移动止盈：盈利达 15% 后启动，回撤 50% 时平仓
- 建议从小资金开始测试

### 3. 飞书报警测试

#### 3.1 验证报警功能
- [ ] 收到测试消息
- [ ] 消息格式正确
- [ ] 报警频率合理（60 秒冷却）

#### 3.2 报警类型
系统会发送以下报警：
- 🔄 市场状态变更
- 🚨 风险事件触发（止损/止盈/暂停）
- 📊 网格创建成功
- ❌ 系统错误

#### 3.3 报警关键词
确保飞书机器人配置了关键词：
- 报警
- 通知
- 网格

### 4. 测试网验证

#### 4.1 运行测试脚本
```bash
cd adaptive_grid_trading
PYTHONPATH=. python3 scripts/test_net.py
```

#### 4.2 验证项目
- [ ] 所有测试通过
- [ ] 飞书收到测试消息
- [ ] 市场数据正常
- [ ] 指标计算准确

#### 4.3 运行系统（测试模式）
```bash
# 后台运行
nohup python3 src/main.py > logs/app.log 2>&1 &

# 查看日志
tail -f logs/adaptive_grid.log
```

#### 4.4 观察指标
- [ ] 系统稳定运行 24 小时
- [ ] 巡检任务按时执行（每小时）
- [ ] 日志无异常错误
- [ ] 飞书报警正常

### 5. 实盘部署

#### 5.1 切换到实盘
1. **修改配置文件**
   ```yaml
   exchange:
     testnet: false  # 改为 false
   ```

2. **更新 API 密钥**
   ```bash
   # config/.env
   BINANCE_API_KEY=your_live_api_key
   BINANCE_API_SECRET=your_live_api_secret
   ```

3. **重启系统**
   ```bash
   # 停止测试网进程
   pkill -f "python.*main.py"
   
   # 启动实盘
   nohup python3 src/main.py > logs/app.log 2>&1 &
   ```

#### 5.2 资金管理
- [ ] **首次入金**: 建议 100-500 USDT（小额测试）
- [ ] **仓位控制**: 单次网格不超过总资金 20%
- [ ] **风险准备**: 做好亏损 8% 的心理准备
- [ ] **盈利预期**: 合理预期，不要贪心

#### 5.3 监控计划
- [ ] 每天查看日志
- [ ] 关注飞书报警
- [ ] 定期检查盈亏
- [ ] 每周评估性能

### 6. 风险控制

#### 6.1 系统风险
- [ ] 设置服务器监控
- [ ] 配置自动重启
- [ ] 定期备份数据库
- [ ] 准备应急方案

#### 6.2 市场风险
- [ ] 避免极端行情
- [ ] 关注重大事件
- [ ] 设置合理止损
- [ ] 不要过度交易

#### 6.3 技术风险
- [ ] 监控网络连接
- [ ] 检查 API 限额
- [ ] 关注系统资源
- [ ] 准备备用方案

### 7. 性能监控

#### 7.1 关键指标
- API 调用成功率：> 99%
- 巡检响应时间：< 10 秒
- 系统可用性：> 99%
- 最大回撤：< 8%

#### 7.2 日志分析
```bash
# 查看错误
grep "ERROR" logs/adaptive_grid.log | tail -n 50

# 查看警告
grep "WARNING" logs/adaptive_grid.log | tail -n 50

# 统计日志级别
cat logs/adaptive_grid.log | awk -F'|' '{print $2}' | sort | uniq -c
```

#### 7.3 性能统计
```bash
# 查看系统运行时间
ps aux | grep main.py

# 查看内存使用
top -pid $(pgrep -f "python.*main.py")
```

### 8. 应急处理

#### 8.1 常见问题
1. **系统异常停止**
   ```bash
   # 重启系统
   nohup python3 src/main.py > logs/app.log 2>&1 &
   ```

2. **飞书报警频繁**
   - 检查市场波动
   - 调整风险参数
   - 考虑暂停系统

3. **API 连接失败**
   - 检查网络
   - 验证 API 密钥
   - 查看币安状态

#### 8.2 紧急暂停
```bash
# 停止系统
pkill -f "python.*main.py"

# 确认停止
ps aux | grep main.py
```

#### 8.3 联系支持
- 币安客服：7x24 在线
- 技术支持：查看日志
- 社区论坛：寻求帮助

### 9. 部署验证

#### 9.1 部署检查
- [ ] 系统正常运行
- [ ] 日志正常输出
- [ ] 飞书报警正常
- [ ] 数据正常更新

#### 9.2 功能验证
- [ ] 市场数据获取正常
- [ ] 指标计算准确
- [ ] 状态识别正确
- [ ] 网格创建成功

#### 9.3 性能验证
- [ ] 巡检按时执行
- [ ] API 调用成功率高
- [ ] 系统资源正常
- [ ] 报警响应及时

## 实盘部署时间表

### 第 1 周：测试网测试
- Day 1-2: 修复 API 连接，完成测试
- Day 3-4: 运行系统，观察行为
- Day 5-7: 优化参数，验证稳定性

### 第 2 周：实盘准备
- Day 1-2: 准备实盘环境
- Day 3-4: 小额资金测试（100 USDT）
- Day 5-7: 观察实盘表现

### 第 3-4 周：逐步扩大
- 根据表现调整参数
- 逐步增加资金
- 建立监控机制
- 形成运维流程

## 成功标准

### 测试网阶段
- ✅ 系统稳定运行 7 天
- ✅ 所有功能正常
- ✅ 报警响应及时
- ✅ 参数经过验证

### 实盘阶段
- ✅ 小额测试盈利
- ✅ 风险控制有效
- ✅ 系统稳定可靠
- ✅ 监控机制完善

## 重要提醒

⚠️ **风险提示**:
1. 量化交易存在亏损风险
2. 历史表现不代表未来
3. 请做好资金管理
4. 不要投入承受不起的资金

⚠️ **注意事项**:
1. 先在测试网充分测试
2. 实盘从小额开始
3. 密切关注系统运行
4. 定期评估和调整

---

**准备时间**: 2026-03-19  
**版本**: v0.1.0  
**状态**: 测试网测试完成，准备实盘部署
