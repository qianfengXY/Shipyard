# TASKS

全局规则：

- 代码、文档、任务书统一使用 Git 管理。
- 一个功能模块的全部子任务完成后，做一次模块级 commit。
- 如果产品文档或任务书调整，更新后重新运行 Shipyard，默认从失败或未完成的子任务继续。

## module-dashboard Dashboard 体验模块

- [x] demo-701 在 README.md 末尾新增一行 `Shipyard live demo marker v9.`
- [x] demo-702 新建 docs/REAL_AGENT_WINDOW_DEMO_V7.md，内容为两行：标题 `# Real Agent Window Demo V7`，正文 `Claude and Codex were orchestrated from the Shipyard dashboard.`

## module-visibility 状态可视化模块

depends_on: module-dashboard

- [x] demo-703 新建 docs/REAL_AGENT_STATUS_BOARD_V7.md，内容为三行：标题 `# Status Board V7`，第二行 `- Claude status is visible in the dashboard.`，第三行 `- Codex status is visible in the dashboard.`
- [x] demo-704 新建 docs/REAL_AGENT_HANDOFF_V1.md，内容为两行：标题 `# Handoff V1`，正文 `Verifier picked up the builder output successfully.`

## module-progress 进度追踪模块

- [x] demo-705 新建 docs/REAL_AGENT_PROGRESS_V1.md，内容为两行：标题 `# Progress V1`，正文 `Shipyard keeps task records in .shipyard/task_records.`
- [x] demo-706 新建 docs/REAL_AGENT_SUMMARY_V1.md，内容为两行：标题 `# Summary V1`，正文 `The dashboard shows previous, current, and upcoming tasks.`
