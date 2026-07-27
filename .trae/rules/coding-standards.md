# 编码标准与规范

## 语言与注释

- 用中文思考和回答。代码注释、日志输出也一律用中文。
- 对外暴露的 API 名称、变量名可使用英文，但注释必须用中文。

## 项目文档引用

- 项目需求：根目录 [README.md](../../README.md)（精简版），完整需求见 `docs/plans/项目需求迭代文档.md`
- 技术方案：`docs/` 目录下的文档，通过 `docs/README.md` 索引查询
- 文档存储：需求类、报告类、部署类、方案类、设计类的文档统一存放在 `docs/` 目录

## 禁止硬编码（强制）🚫

所有业务逻辑参数、评分、阈值、延迟时间等必须通过配置文件或算法计算得出，严禁在代码中直接写死固定值。

```python
# ❌ 禁止：硬编码阈值
if score > 0.8:
    place_order()

# ✅ 正确：从配置读取
if score > config.get("trade.score_threshold"):
    place_order()
```

代码检测员必须检查此项。

## 禁止服务器回测（强制）🚫

严禁在服务器上直接运行回测。回测必须在本机执行，数据来源：

1. 在服务器端筛选目标数据后，下载到本地进行回测
2. 通过本地代码从服务器获取数据后，在本地进行回测

```bash
# ❌ 禁止：SSH 到服务器执行回测脚本
ssh root@server "python backtest/run.py"

# ✅ 正确：从服务器拉取数据到本地，再本地回测
scp root@server:/data/btc_klines.csv ./data/
python backtest/run.py --data ./data/btc_klines.csv
```

原因：回测消耗大量 CPU/内存，会与生产交易程序抢占资源，可能导致交易延迟或服务中断。

## 框架与依赖

- 优先使用项目已有依赖，新增依赖需在 `requirements.txt` 或 `pyproject.toml` 中声明
- 禁止使用已废弃的 API 或库版本

---

**最后更新：** 2026-06-01