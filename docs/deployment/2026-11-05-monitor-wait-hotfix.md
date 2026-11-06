# 2026-11-05 宿主机监控修复发布

用户明确授权“部署吧”。环境：root@43.156.242.184，/root/stockfilter_v3。
发布范围仅 scripts/monitor.sh，使用现有 monitor_deploy.py 的备份、原子替换、单实例、就绪和稳定观察流程；未构建/重启三个业务容器，未提交或推送 Git。

## 结果

北京时间 2026-11-05 08:29:58 发布成功。本地与生产 monitor.sh SHA-256 一致：
`1fc7e8cd7dc4cc3405bb9ea662f2a7fa6787ee9ee49c4a9b48694e4f6f4367a4`。

新监控 PID=84362，PPid=1，SID=PGID=84362；logs/monitor.ready=84362。发布辅助程序 phase=complete。08:29:48 初始化完成，08:29:52 解析监控目标 2026-11-04，08:29:56 完成首轮检查；本轮未出现错误/异常业务告警。部署日志：deploy_logs/20261105.log 的 MONITOR_DEPLOY_SUCCESS。

三个容器发布前后 ID 不变，均 Up 9 days (healthy)：
- stockfilter-obpc：a26eec66c79a
- stockfilter-eadvisor：109bf7cfc72c
- stockfilter-kline：ebb0e188ee56

宿主 .env 与 OBPC 容器配置核对一致：SCHEDULE_SCAN=23:15，KLINE_WAIT_TIMEOUT=1800。
在服务器实际脚本提取纯窗口判断函数，隔离时间烟雾测试：23:17:33 返回等待，23:45:00 返回截止、不再豁免。没有通过该测试发送飞书或写数据库；未等待今晚真实调度，不能将烟测称为晚间实跑验证。监控正常启动流程发送了自身启动通知。

## 发布中发现并闭环的问题

前两次切换被现有辅助程序的多 PID 检查拒绝，两次均自动恢复旧脚本（SHA-256 befe880cb234c9def37cc58cfcc8a09647585e5b48a60e0fe2a4d83f3eb73eff）及唯一旧监控，恢复 PID 分别为77064、79266。

根因：monitor_pids 枚举与读取 PPid 之间，短命 Bash 子进程可能退出；旧实现把父进程读取失败的陈旧 PID 当独立实例。新增确定性复现先得到 1 failed / 13 passed。修复为首次匹配缓存 PPid、返回根 PID 前重验身份，仅排除明确退出或身份变化的候选，活着但父关系未知的实例仍计入，其他读取异常仍失败。没有缩短稳定时长或跳过单实例检查。

修复后监控与操作测试 151 passed；独立上下文复审辅助程序14 passed，未发现阻断问题。Ruff选定规则、git diff --check通过。辅助程序制品SHA-256：`66499d5e4bdd7c47f025ae3b73f5c32d334e86d823ba7d79eec09105d3f6f9aa`。

## 制品和回滚点

远端发布目录：`/root/stockfilter_v3/deploy_logs/monitor-wait-20261105-1fc7e8cd/`。
- monitor.sh：本次候选。
- monitor_deploy.py：原辅助程序，保留追溯。
- monitor_deploy_fixed.py：经复现/修复/复审的辅助程序。
- state、state-retry：两次失败后的恢复记录。
- state-fixed/monitor.sh.old：旧脚本备份。
- state-fixed/monitor-state.json：成功发布状态。

如需回滚，先核对唯一监控 PID，通过修正版辅助程序为旧脚本创建新的 pause/activate/verify/complete 流程；不要直接对 complete 状态调用 restore（该调用按设计不操作）。

本次为已授权宿主机热修，Git 尚未提交/推送。未来常规发布应包含本地 monitor.sh 与 monitor_deploy.py 修改，否则可能由旧版本覆盖。原事故与269项业务回归见 [修复报告](../reports/2026-11-05-obpc-initial-wait-monitor.md)。
