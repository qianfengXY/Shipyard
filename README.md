# Shipyard

Shipyard 是一个本地可运行的最小 Builder-Verifier 编排系统。第一版使用 `docs/TASKS.md` 作为任务源，使用 `.shipyard/` 持久化状态与 artifact，并通过 mock builder / mock verifier 跑通完整状态机闭环。

## 本地运行

要求：

- Python 3.11

安装：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
```

常用命令：

```bash
python -m shipyard.main status
python -m shipyard.main report
python -m shipyard.main watch
python -m shipyard.main step
python -m shipyard.main run
python -m shipyard.main reset
python -m pytest
```

运行说明：

- 任务源来自 `docs/TASKS.md`
- 运行状态保存在 `.shipyard/state.json`
- Builder artifact 保存在 `.shipyard/artifacts/builder/`
- Verifier artifact 保存在 `.shipyard/artifacts/verifier/`
- `run` 会持续执行直到 `COMPLETED` 或 `ABORTED`
- `step` 只推进一步状态机，便于调试和续跑
- `report` 会输出当前任务汇总，包括每个 task 的 builder / verifier 结果
- `watch` 会持续刷新 CLI 监控视图，适合观察 Claude 与 Codex 的接力状态
- `reset` 会清空 `.shipyard/state.json`、`.shipyard/run.log` 和 `.shipyard/artifacts/`

## 真实 Builder / Verifier

默认配置使用 `mock_builder` 和 `mock_verifier`。如果要切到真实 CLI 适配器，可以把 `.shipyard/config.json` 改成：

```json
{
  "builder_adapter": "claude_builder",
  "verifier_adapter": "codex_verifier",
  "max_builder_retries": 3,
  "max_verifier_retries": 3,
  "final_review_commands": ["python -m pytest"],
  "claude_command": "claude",
  "codex_command": "codex",
  "mock_builder_failures": {},
  "mock_verifier_failures": {}
}
```

使用前提：

- 本机已安装并登录 `claude`
- 本机已安装并登录 `codex`
- 建议先激活仓库内 `.venv`

说明：

- `claude_builder` 会调用 `claude` CLI 在当前仓库内实现任务，并按 Builder artifact 协议返回结果
- `codex_verifier` 会调用 `codex exec` 以只读方式做独立验收，并按 Verifier artifact 协议返回结果
- 这两者都不会直接改 `.shipyard/state.json` 或 `docs/TASKS.md`，状态推进仍由 Shipyard Engine 负责

Real agent demo completed.
Shipyard live demo marker.
Shipyard live demo marker v2.
Shipyard live demo marker v3.
Shipyard live demo marker v4.
Shipyard live demo marker v5.
Shipyard live demo marker v6.
Shipyard live demo marker v7.
Shipyard live demo marker v8.
Shipyard live demo marker v9.
