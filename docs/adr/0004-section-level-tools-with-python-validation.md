# section 级工具 + 回调 Python 校验

status: accepted
date: 2026-09-01

旧链路让模型整份重发 `optimized_resume_data`（≈10KB JSON），延迟由输出 token 决定（实测 30~50s）。新架构用 Agent 工具做增量修改：Agent 调用 `update_section` 类工具（section 粒度；`basics` 细化到字段级）提交修改，工具回调 FastAPI 执行校验并落库——数值防幻觉（`_unverified_numeric_claims` 语义）与待确认变更（pending_change）机制保留在回调链路中，快照保护与分级合并因工具化的增量性自然简化。

## Considered Options

- **字段级工具**：粒度最细但轮次多，agent 循环反而变慢，拒绝。
- **纯提案制**（工具只返回提案，不落库）：最保守但延迟收益打折，且与现有 pending_change 决策流重复，拒绝。
- **section 级工具 + 回调校验**（采纳）：单次工具调用输出从 10KB 降至 KB 级，目标延迟 ~5s；信任边界仍留在服务端。

## Consequences

- 旧四层防御中的「Prompt 保留约束」「快照保护」「分级合并」代码随 LangChain 退役删除；「数值防幻觉」「待确认人审」必须在 FastAPI 回调端点中重新实现，语义不变。
- list 类 section 的重排序/id 变化检测不再是问题：工具以 id 定位条目，未提及的条目不被触碰。
