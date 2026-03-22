# Shipyard SPEC

## 1. 项目名称

Shipyard

---

## 2. 仓库信息

- GitHub Repository: `https://github.com/qianfengXY/Shipyard.git`
- Project Name: `Shipyard`

约定：

- 项目根目录为仓库根目录
- 本地运行状态保存在 `.shipyard/`
- 文档保存在 `docs/`
- Python 源码保存在 `src/shipyard/`

---

## 3. 项目目标

实现一个本地可运行的最小系统 `Shipyard`，用于驱动 Builder–Verifier 工作流。

当前工作流定义如下：

1. 读取产品文档和任务清单
2. 选择当前最高优先级未完成任务
3. 调用 Builder 执行开发
4. Builder 自测通过后，调用 Verifier 做独立验收
5. Verifier 通过则标记任务完成并进入下一任务
6. Verifier 不通过则生成修复建议并回流给 Builder
7. 所有任务完成后，执行一次最终总验收
8. 总验收通过后，整个流程完成

第一版只实现最小可运行闭环：

- 本地命令行运行
- 文件持久化状态
- mock Builder / mock Verifier
- 可中断后续跑
- 可自动勾选任务
- 可完成一轮完整状态流转

---

## 4. 第一版边界

### 4.1 本版必须实现

- 本地命令行运行
- Python 3.11
- `run / step / status / reset` CLI
- 使用 `docs/TASKS.md` 作为任务源
- 使用 `.shipyard/state.json` 保存状态
- 使用 `.shipyard/artifacts/` 保存 Builder / Verifier 输出
- 支持 mock Builder
- 支持 mock Verifier
- 支持最终总验收
- 支持中断后恢复运行
- 支持自动把完成任务从 `[ ]` 改为 `[x]`

### 4.2 本版不实现

- 不接真实 Claude Agent SDK
- 不接真实 Codex SDK / CLI
- 不做 Web UI
- 不做分布式队列
- 不做数据库
- 不做多项目并发
- 不做 GitHub Actions / PR 自动化
- 不做 webhook
- 不做复杂 PRD 结构化解析
- 不做权限系统

---

## 5. 核心角色定义

### 5.1 Builder
Builder 负责：

- 读取任务与开发约束
- 执行开发
- 运行自测
- 输出交验结果

第一版使用 `mock_builder`

后续真实接入目标：
- Claude

### 5.2 Verifier
Verifier 负责：

- 读取任务与验收约束
- 独立做验收
- 输出 PASS / FAIL
- FAIL 时给出修复建议

第一版使用 `mock_verifier`

后续真实接入目标：
- Codex

### 5.3 Shipyard Engine
Shipyard Engine 负责：

- 读取状态
- 调度 Builder / Verifier
- 维护状态机
- 写回状态
- 推进任务
- 触发最终总验收

---

## 6. 推荐目录结构

```text
Shipyard/
  SPEC.md
  README.md
  pyproject.toml

  docs/
    PRD.md
    TASKS.md
    DEV_SPEC.md
    ACCEPTANCE_SPEC.md

  .shipyard/
    config.json
    state.json
    run.log
    artifacts/
      builder/
      verifier/

  src/
    shipyard/
      __init__.py
      main.py
      engine.py
      models.py
      config.py
      logger.py
      state_store.py
      task_parser.py
      repository.py

      adapters/
        __init__.py
        builder_base.py
        verifier_base.py
        mock_builder.py
        mock_verifier.py
        claude_builder.py
        codex_verifier.py

      services/
        __init__.py
        task_selector.py
        handoff_service.py
        transition_service.py
        completion_service.py

  tests/
    test_task_parser.py
    test_state_store.py
    test_engine_flow.py
    test_resume_flow.py
```

---

## 7. CLI 设计

Shipyard 必须提供以下 CLI：

```bash
python -m shipyard.main run
python -m shipyard.main step
python -m shipyard.main status
python -m shipyard.main reset
```

### 7.1 `run`
持续运行状态机，直到：

- 进入 `COMPLETED`
- 或进入 `ABORTED`

### 7.2 `step`
只推进一步状态机，便于调试与测试。

### 7.3 `status`
打印当前运行状态，至少包括：

- 当前 phase
- 当前 task_id
- 当前 task_title
- builder_attempt
- verifier_attempt
- completed count / total count
- last_error

### 7.4 `reset`
重置以下内容：

- `.shipyard/state.json`
- `.shipyard/run.log`
- `.shipyard/artifacts/`

但不删除：

- `docs/`
- 源代码
- 测试代码

---

## 8. 状态机设计

### 8.1 Phase 枚举

第一版必须支持以下 phase：

- `INIT`
- `SELECT_TASK`
- `BUILDER_RUNNING`
- `READY_FOR_VERIFICATION`
- `VERIFIER_RUNNING`
- `TASK_DONE`
- `FINAL_REVIEW`
- `COMPLETED`
- `ABORTED`

### 8.2 状态流转

```text
INIT
  -> SELECT_TASK

SELECT_TASK
  -> BUILDER_RUNNING        (存在未完成任务)
  -> FINAL_REVIEW           (全部任务已完成)

BUILDER_RUNNING
  -> READY_FOR_VERIFICATION (Builder 自测通过)
  -> BUILDER_RUNNING        (Builder 自测失败且未超重试)
  -> ABORTED                (Builder 超过重试上限)

READY_FOR_VERIFICATION
  -> VERIFIER_RUNNING

VERIFIER_RUNNING
  -> TASK_DONE              (Verifier PASS)
  -> BUILDER_RUNNING        (Verifier FAIL，回流返工)
  -> ABORTED                (Verifier 超过重试上限)

TASK_DONE
  -> SELECT_TASK

FINAL_REVIEW
  -> COMPLETED
  -> ABORTED
```

---

## 9. 文档协议

### 9.1 `docs/TASKS.md`

使用 markdown checkbox 格式：

```md
# TASKS

- [ ] task-001 初始化项目骨架
- [ ] task-002 实现注册页 UI
- [ ] task-003 接入注册 API
- [ ] task-004 编写注册流程测试
```

解析规则：

- 未完成：`- [ ] task-id 标题`
- 已完成：`- [x] task-id 标题`
- `task-id` 必须唯一
- 文件中靠前的任务优先级更高

### 9.2 `docs/PRD.md`
纯文本读取，不做结构化解析。

### 9.3 `docs/DEV_SPEC.md`
纯文本读取，不做结构化解析。

### 9.4 `docs/ACCEPTANCE_SPEC.md`
纯文本读取，不做结构化解析。

---

## 10. `.shipyard/` 工作目录协议

### 10.1 `.shipyard/config.json`

默认示例：

```json
{
  "builder_adapter": "mock_builder",
  "verifier_adapter": "mock_verifier",
  "max_builder_retries": 3,
  "max_verifier_retries": 3,
  "final_review_commands": [
    "pnpm lint",
    "pnpm build",
    "pnpm test"
  ]
}
```

### 10.2 `.shipyard/state.json`

示例：

```json
{
  "run_id": "run_2026_03_21_001",
  "phase": "BUILDER_RUNNING",
  "current_task_id": "task-002",
  "current_task_title": "实现注册页 UI",
  "builder_attempt": 1,
  "verifier_attempt": 0,
  "final_review_attempt": 0,
  "completed_task_ids": ["task-001"],
  "failed_task_ids": [],
  "last_builder_result_path": ".shipyard/artifacts/builder/task-002-result.json",
  "last_verifier_result_path": null,
  "last_error": null,
  "created_at": "2026-03-21T10:00:00+09:00",
  "updated_at": "2026-03-21T10:10:00+09:00"
}
```

要求：

- 每次 phase 变化都必须落盘
- 每次 attempt 变化都必须落盘
- 支持 load-or-init
- 必须使用原子写入

### 10.3 `.shipyard/run.log`

要求：

- 记录每一步状态推进
- 记录 Builder / Verifier 执行摘要
- 记录错误

第一版可使用普通文本日志。

### 10.4 `.shipyard/artifacts/`

目录：

```text
.shipyard/artifacts/
  builder/
  verifier/
```

要求：

- Builder 结果保存在 `builder/`
- Verifier 结果保存在 `verifier/`

---

## 11. Builder 输出协议

路径示例：

`.shipyard/artifacts/builder/task-002-result.json`

格式：

```json
{
  "task_id": "task-002",
  "status": "SELF_TEST_PASSED",
  "summary": "已完成注册页 UI，并通过 lint、unit test、build",
  "files_changed": [
    "src/pages/register.tsx",
    "src/components/register-form.tsx"
  ],
  "self_test_commands": [
    "pnpm lint",
    "pnpm test register",
    "pnpm build"
  ],
  "self_test_results": [
    {
      "command": "pnpm lint",
      "exit_code": 0,
      "passed": true
    },
    {
      "command": "pnpm test register",
      "exit_code": 0,
      "passed": true
    },
    {
      "command": "pnpm build",
      "exit_code": 0,
      "passed": true
    }
  ],
  "claimed_acceptance": [
    "页面可渲染",
    "表单字段可输入",
    "错误提示区域已预留"
  ],
  "next_handoff": "VERIFIER",
  "generated_at": "2026-03-21T10:11:00+09:00"
}
```

允许的 `status`：

- `SELF_TEST_PASSED`
- `SELF_TEST_FAILED`
- `BLOCKED`

要求：

- Builder 只返回结果，不改状态机
- Builder 不直接修改 `.shipyard/state.json`
- Builder 不直接修改 `TASKS.md`

---

## 12. Verifier 输出协议

路径示例：

`.shipyard/artifacts/verifier/task-002-review.json`

格式：

```json
{
  "task_id": "task-002",
  "status": "FAIL",
  "summary": "UI 基本完成，但移动端布局不满足验收要求",
  "findings": [
    {
      "severity": "high",
      "title": "375px 下按钮遮挡错误提示",
      "evidence": "本地验收截图与 DOM 检查显示错误提示区域发生重叠",
      "expected": "错误提示在 375px 宽度下完整可见",
      "suggested_fix": "调整按钮区域 margin-top，并为错误提示保留最小高度"
    }
  ],
  "verification_commands": [
    "pnpm lint",
    "pnpm build",
    "pnpm test register",
    "pnpm test:e2e register-ui"
  ],
  "decision": "REWORK_REQUIRED",
  "generated_at": "2026-03-21T10:14:00+09:00"
}
```

允许的 `status`：

- `PASS`
- `FAIL`
- `BLOCKED`

要求：

- FAIL 时至少提供一条 finding
- Verifier 只返回结果，不改状态机
- Verifier 不直接修改 `.shipyard/state.json`
- Verifier 不直接修改 `TASKS.md`

---

## 13. Python 数据模型

建议使用 `dataclasses`。

### 13.1 `TaskItem`

```python
@dataclass
class TaskItem:
    task_id: str
    title: str
    done: bool
```

### 13.2 `OrchestratorState`

```python
@dataclass
class OrchestratorState:
    run_id: str
    phase: str
    current_task_id: str | None
    current_task_title: str | None
    builder_attempt: int
    verifier_attempt: int
    final_review_attempt: int
    completed_task_ids: list[str]
    failed_task_ids: list[str]
    last_builder_result_path: str | None
    last_verifier_result_path: str | None
    last_error: str | None
    created_at: str
    updated_at: str
```

---

## 14. 适配器接口

### 14.1 BuilderAdapter

文件：`src/shipyard/adapters/builder_base.py`

```python
from typing import Protocol

class BuilderAdapter(Protocol):
    def run(
        self,
        task_id: str,
        task_title: str,
        docs_context: dict,
        prior_review: dict | None,
        state: dict,
    ) -> dict:
        ...
```

### 14.2 VerifierAdapter

文件：`src/shipyard/adapters/verifier_base.py`

```python
from typing import Protocol

class VerifierAdapter(Protocol):
    def run(
        self,
        task_id: str,
        task_title: str,
        docs_context: dict,
        builder_result: dict,
        state: dict,
    ) -> dict:
        ...
```

要求：

- 适配器必须返回符合协议的 dict
- 适配器不得直接操控状态机
- 适配器必须可以被替换

---

## 15. Mock 适配器定义

### 15.1 `mock_builder.py`

最小行为：

- 默认对每个 task 返回 `SELF_TEST_PASSED`
- 可通过配置让某 task 第一次返回 `SELF_TEST_FAILED`，第二次返回 `SELF_TEST_PASSED`
- 输出符合 Builder 协议的结果

### 15.2 `mock_verifier.py`

最小行为：

- 默认对每个 task 返回 `PASS`
- 可通过配置让某 task 第一次 `FAIL`，第二次 `PASS`
- FAIL 时必须带至少一条 finding
- 输出符合 Verifier 协议的结果

目的：

- 用于测试完整状态流转
- 用于测试回流与重试
- 用于测试中断续跑

---

## 16. docs_context 结构

第一版只收集文本，不做语义解析。

结构如下：

```json
{
  "prd_text": "...",
  "tasks_text": "...",
  "dev_spec_text": "...",
  "acceptance_spec_text": "...",
  "current_task": {
    "task_id": "task-002",
    "title": "实现注册页 UI"
  }
}
```

要求：

- Builder 收到：
  - `prd_text`
  - `tasks_text`
  - `dev_spec_text`
  - `current_task`

- Verifier 收到：
  - `prd_text`
  - `tasks_text`
  - `acceptance_spec_text`
  - `current_task`

---

## 17. 核心模块职责

### 17.1 `task_parser.py`
负责：

- 解析 `docs/TASKS.md`
- 返回任务列表
- 标记指定任务为完成

### 17.2 `state_store.py`
负责：

- 初始化 `.shipyard/state.json`
- 读取 state
- 原子写回 state

### 17.3 `config.py`
负责：

- 读取 `.shipyard/config.json`
- 提供默认配置
- 校验必要字段

### 17.4 `task_selector.py`
负责：

- 选择第一个未完成任务
- 若无任务则返回 `None`

### 17.5 `handoff_service.py`
负责：

- 保存 Builder artifact
- 保存 Verifier artifact
- 读取某个 task 最近一次 Builder 输出
- 读取某个 task 最近一次 Verifier 输出

### 17.6 `transition_service.py`
负责：

- 根据 Builder / Verifier 结果决定下一 phase

### 17.7 `completion_service.py`
负责：

- 判断任务是否全部完成
- 执行最终总验收

### 17.8 `engine.py`
负责：

- 主状态机
- 调用适配器
- 推进 phase
- 更新 state
- 标记任务完成
- 进入最终总验收

### 17.9 `main.py`
负责：

- CLI 入口
- 参数解析
- 调用 engine

---

## 18. 主循环规范

`run` 模式下，主循环应遵循以下逻辑：

1. 若 phase 为 `INIT`，切换到 `SELECT_TASK`
2. 若 phase 为 `SELECT_TASK`
   - 有未完成任务：切到 `BUILDER_RUNNING`
   - 无未完成任务：切到 `FINAL_REVIEW`
3. 若 phase 为 `BUILDER_RUNNING`
   - 调用 Builder
   - 若 Builder 返回 `SELF_TEST_PASSED`：切到 `READY_FOR_VERIFICATION`
   - 若 Builder 返回 `SELF_TEST_FAILED`：
     - 未达上限：保持 `BUILDER_RUNNING`
     - 超上限：切到 `ABORTED`
4. 若 phase 为 `READY_FOR_VERIFICATION`，切到 `VERIFIER_RUNNING`
5. 若 phase 为 `VERIFIER_RUNNING`
   - 调用 Verifier
   - 若 `PASS`：标记 task done，切到 `TASK_DONE`
   - 若 `FAIL`：
     - 未达上限：切回 `BUILDER_RUNNING`
     - 超上限：切到 `ABORTED`
6. 若 phase 为 `TASK_DONE`，切到 `SELECT_TASK`
7. 若 phase 为 `FINAL_REVIEW`
   - 通过：切到 `COMPLETED`
   - 失败：切到 `ABORTED`

---

## 19. 重试策略

从 `.shipyard/config.json` 读取：

- `max_builder_retries`
- `max_verifier_retries`

规则：

- Builder 自测失败累计次数超过上限，进入 `ABORTED`
- Verifier 验收失败累计次数超过上限，进入 `ABORTED`

注意：

- 每进入新任务时，`builder_attempt` 和 `verifier_attempt` 重置为 0
- `TASK_DONE` 后清空当前 task 信息

---

## 20. 最终总验收

当 `TASKS.md` 中所有任务均完成后，进入 `FINAL_REVIEW`

### 20.1 命令来源
从 `.shipyard/config.json` 读取：

```json
{
  "final_review_commands": [
    "pnpm lint",
    "pnpm build",
    "pnpm test"
  ]
}
```

### 20.2 成功条件
同时满足：

1. 所有 final review commands 成功
2. `TASKS.md` 所有任务均为 `[x]`
3. 当前不存在挂起 task

否则进入 `ABORTED`

---

## 21. 错误处理

必须处理以下异常场景：

- `docs/TASKS.md` 不存在
- `docs/TASKS.md` 为空
- task id 重复
- `.shipyard/config.json` 缺失或非法
- `.shipyard/state.json` 损坏
- artifact 目录缺失
- adapter 返回非法 status
- 最终总验收命令执行失败

要求：

- 写入 `last_error`
- 输出清晰错误信息
- 必要时进入 `ABORTED`

---

## 22. 测试要求

必须有自动化测试，至少覆盖以下内容。

### 22.1 `test_task_parser.py`
覆盖：

- 正常解析未完成任务
- 正常解析已完成任务
- 重复 task id 报错
- 标记任务完成成功

### 22.2 `test_state_store.py`
覆盖：

- state 初始化
- state 读写
- 原子写入
- state 损坏时报错

### 22.3 `test_engine_flow.py`
覆盖：

- mock builder + mock verifier 跑完整闭环
- 任务自动从 `[ ]` 改为 `[x]`
- 最终 phase 为 `COMPLETED`

### 22.4 `test_resume_flow.py`
覆盖：

- 中途停止
- 再次运行后从原状态续跑
- 已完成任务不丢失
- 不会重复初始化

---

## 23. 代码质量要求

- Python 3.11
- 类型注解完整
- 模块职责明确
- 结构清晰
- 易于替换适配器
- 不要过度设计
- 第一版优先保证最小可运行闭环
- 不要把所有逻辑塞进一个文件
- 保留 `claude_builder.py` 和 `codex_verifier.py` 占位文件

---

## 24. 后续扩展预留

第一版不实现，但设计上必须方便后续接入：

### 24.1 Claude Builder
后续将 `claude_builder.py` 接到真实 Claude。

### 24.2 Codex Verifier
后续将 `codex_verifier.py` 接到真实 Codex。

### 24.3 Background 执行
后续允许把长时间 Builder / Verifier 执行放到后台。

### 24.4 更复杂工作流
后续允许支持：
- 多 verifier
- 多阶段验收
- Git commit checkpoints
- CI 集成

---

## 25. 实现顺序建议

建议按以下顺序实现：

1. `models.py`
2. `task_parser.py`
3. `state_store.py`
4. `config.py`
5. `mock_builder.py`
6. `mock_verifier.py`
7. `handoff_service.py`
8. `task_selector.py`
9. `completion_service.py`
10. `engine.py`
11. `main.py`
12. `tests/`

---

## 26. 交付标准

实现完成后，以下命令必须可用：

```bash
python -m shipyard.main reset
python -m shipyard.main status
python -m shipyard.main step
python -m shipyard.main run
```

并满足：

1. 能从 `docs/TASKS.md` 读取任务
2. 能维护 `.shipyard/state.json`
3. 能保存 Builder / Verifier artifacts
4. 能自动勾选完成任务
5. 能处理中断后续跑
6. 能通过测试
7. mock 闭环能跑到 `COMPLETED`

---

## 27. 给 Codex 的实现要求

请直接基于本 SPEC 开发第一版 Shipyard。

要求：

- 使用 Python 3.11
- 先完成最小可运行闭环
- 先使用 mock Builder / mock Verifier
- 保留真实 Claude / Codex 接入占位
- 实现 CLI
- 实现自动测试
- 保证可中断恢复
- 保证文件落盘协议稳定

不要：

- 不要引入不必要的复杂抽象
- 不要提前做云端架构
- 不要做 Web UI
- 不要跳过测试
- 不要把 Builder / Verifier 写死到 engine 中
