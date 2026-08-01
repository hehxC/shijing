# Issue tracker: GitHub

本仓库的问题和 PRD 使用 GitHub Issues 管理，所有操作通过 `gh` CLI 完成。

## 常用操作

- 创建：`gh issue create --title "..." --body "..."`
- 查看：`gh issue view <number> --comments`
- 列表：`gh issue list --state open`
- 评论：`gh issue comment <number> --body "..."`
- 添加标签：`gh issue edit <number> --add-label "..."`
- 移除标签：`gh issue edit <number> --remove-label "..."`
- 关闭：`gh issue close <number> --comment "..."`

仓库由当前目录的 `git remote -v` 自动确定。

## Pull requests as a triage surface

**PRs as a request surface: no.**

GitHub Issues 与 PR 共用编号空间。遇到 `#42` 时，先尝试
`gh pr view 42`，失败后再使用 `gh issue view 42`。

## 技能约定

- “publish to the issue tracker”：创建 GitHub Issue。
- “fetch the relevant ticket”：运行 `gh issue view <number> --comments`。
- Wayfinder 地图使用带有 `wayfinder:map` 标签的 Issue。
- 子任务优先使用 GitHub sub-issues；不可用时使用任务列表和
  `Part of #<map>`。
- 阻塞关系优先使用 GitHub 原生 issue dependencies。
- 领取任务时使用 `gh issue edit <number> --add-assignee @me`。
