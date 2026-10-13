---
id: stockfilter_v3-20261013-pg-container-vanished-dns
类型: issue
项目: stockfilter_v3
标题: common_service_postgres 容器消失致 K 线容器 DNS 解析失败
日期: 2026-09-17
标签: [bug, 部署, 运维]
任务标识: K线容器DNS故障排查与恢复_2026-09-17.md
状态: 待复查
审查: 已审查
---

# common_service_postgres 容器消失致 K 线容器 DNS 解析失败

## 现象
监控通知「容器 stockfilter-kline 日志中发现 N 个错误：PostgreSQL 数据库连接失败 could not translate host name common_service_postgres」→ 造成 9/16 22:00 更新失败 + 9/17 07:00 补全失败，9/16 数据完全缺失；9/18 **再次复发**（9/17 数据缺失）。

## 根因
- (a) `common_service_postgres` 容器**整体消失**（`docker ps -a` 无此容器），但数据卷 `common_service_postgres_data` 完整保留 → DNS 解析不到主机名。触发点：one_click_deploy.sh 执行 `docker-compose down` + `build`，down 后若 build 失败 postgres 容器永久消失。
- (b) 复发根因：旧 watchdog 用相对 `docker-compose`，cron `PATH=/sbin:/bin:/usr/sbin:/usr/bin` 不含 `/usr/local/bin` → 一直 command not found，兜底失败。

## 解决
恢复 PG（`cd /root/common_service && docker-compose up -d postgres`）；本地修复 one_click_deploy.sh（删除 down+build，改 `docker-compose pull postgres redis` + `docker-compose up -d --force-recreate postgres redis` + 健康等待 WAIT_RETRIES=30×2s + 防幻觉验证）；watchdog 改为绝对路径 `/usr/local/bin/docker-compose`（9/18 11:56:33 改、11:56:43 验证成功）。

## 验证
PG 恢复后 Up healthy；数据无损已验证（9/15 及之前 klines 2909/2910 只完整）；kline 容器内 `DatabaseManager` 连接成功、最新交易日正确回退到 2026-09-15（证明 9/16 缺失）；本地脚本修复通过规范检查/幻觉测试/深度审查；watchdog 绝对路径 11:56:43 验证成功；9/17 数据已补齐 2898+2。

## 适用范围与修订
common_service/one_click_deploy.sh、服务器 cron watchdog。**遗留**：本地修复版待同步到服务器 `/root/common_service/one_click_deploy.sh`（MD5 对比 + `bash -n` 语法检查，尚未完成）；本地与服务器 docker-compose.yml 不一致（本地 4 服务含 notification_service/nginx，服务器精简 2 服务）。无替代条目。

## 教训
部署脚本里的 `down` 是有代价的破坏性操作——down 成功但后续 build/up 失败会让服务永久下线（数据卷虽在，但容器已消失）；应改为 `pull` + `up -d --force-recreate` 并加健康等待与防幻觉校验。另：cron 环境的 PATH 与交互式 shell 不同，脚本内引用二进制（如 docker-compose）必须用绝对路径。（AI 归纳，仅供参考）

## 关联
- 本地补充来源: [handoff](../../handoffs/K线容器DNS故障排查与恢复_2026-09-17.md)（不随 Git 同步）
- 持久证据: common_service/one_click_deploy.sh（本地已修复版）；服务器 /root/common_service/docker-compose.yml；数据卷 common_service_postgres_data
- 必要证据摘要: PG Up healthy；9/15 及之前 klines 2909/2910 完整；最新交易日回退 2026-09-15；watchdog 绝对路径验证成功；本地脚本待同步服务器
- 相关: 无