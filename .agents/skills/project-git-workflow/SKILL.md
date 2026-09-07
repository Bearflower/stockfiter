---
name: project-git-workflow
description: 用户明确要求暂存、提交、推送或执行本项目 `/git` 工作流时使用；普通开发或只读 Git 检查不使用。
---

# 股票筛选项目 Git 工作流

完整读取 `.trae/rules/git-workflow.md`。只有用户明确要求的 Git 写操作才被授权：提交不等于推送，推送不等于部署；`/git` 表示提交并推送，但仍不得自动部署。

不得修改 Git 配置，不得执行 `push --force`、`reset --hard`、`checkout .`、`restore .`、`clean -f`、`branch -D` 或等价的破坏性命令。不要使用 `git add -A`、`git add .` 或宽泛 glob 暂存未知变更。

1. 运行 `git status --short --branch`，确认分支、上游和所有用户改动。
2. 使用 `git diff`、`git diff --cached` 检查实际内容；运行 `git log --oneline -5` 了解提交风格。
3. 确认相关验证已运行且结果新鲜；检查暂存范围没有 `.env`、凭证、数据库、日志、缓存和大型产物。
4. 仅使用 `git add <明确文件>` 暂存本次任务相关文件，并再次检查 `git diff --cached`。
5. 提交信息简洁表达改动目的；未经要求不重写已有提交。
6. 仅在用户要求推送时推送。先检查 upstream；若无 upstream，使用当前分支设置远端跟踪。
7. 推送因远端更新失败时停止并汇报；不要强推绕过，也不要擅自 rebase 或合并。
