# Git 工作流规范

## /git 命令

用户输入 `/git` 时：

1. 简述当前任务概要
2. 将概要作为 commit message 提交到 git
3. Push 到服务器

## 提交安全规则

- **禁止**修改 git config
- **禁止**执行破坏性命令：`push --force`、`reset --hard`、`checkout .`、`restore .`、`clean -f`、`branch -D`
- **禁止** force push 到 main/master 分支
- **禁止**在未经用户明确要求的情况下提交代码

## 提交流程

1. 运行 `git status` 查看变更（不要使用 `-uall` 参数）
2. 运行 `git diff` 查看具体改动
3. 运行 `git log --oneline -5` 查看最近提交风格（保持一致性）
4. 编写 commit message：简洁描述"改动原因"而非"改动内容"
5. 使用 `git add <具体文件>` 暂存（不要用 `git add -A`）
6. 执行 `git commit` 和 `git push`

## 推送规范

- 推送前检查分支是否 track 远程分支：`git branch -vv`
- 若无 upstream，使用 `git push -u origin <branch>`
- 若 push 失败因远程有更新，建议 `git pull --rebase`，不要 force push

---

**最后更新：** 2026-06-01