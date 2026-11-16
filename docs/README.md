# docs 文档索引

本仓库文档的唯一入口。**先读 [`PROJECT-OVERVIEW.md`](./PROJECT-OVERVIEW.md)**——它是架构与功能的单一事实源，其余文档都是它的展开或专题补充。

## 按场景找文档

| 我要做什么 | 读这篇 |
| --- | --- |
| 第一次接触项目，搞清有哪些模块、怎么跑起来的 | [`PROJECT-OVERVIEW.md`](./PROJECT-OVERVIEW.md) |
| 把项目跑起来（本地开发 / 全栈 Docker） | [`本地开发环境启动指南.md`](./本地开发环境启动指南.md) |
| 改前端 UI、定样式或组件规范 | [`design-system.md`](./design-system.md) |
| 想把界面改成 WorkBuddy 那种观感 | [`design/workbuddy-visual-reference.md`](./design/workbuddy-visual-reference.md) |
| 改 AI 对话链路、提示词、技能或工具 | [`ai/resume-agent.md`](./ai/resume-agent.md) → [`ai/ai-call-flows.md`](./ai/ai-call-flows.md) |
| 排查「AI 改简历为什么没生效 / 待确认变更怎么走」 | [`ai/ai-chat-pending-change.md`](./ai/ai-chat-pending-change.md) |
| 改简历 JSON 结构、加字段或加模块 | [`data/resume-schema.md`](./data/resume-schema.md) |
| 想知道「为什么当初不用另一种方案」 | [`architecture/pi-agent重构设计.md`](./architecture/pi-agent重构设计.md) 与 `PROJECT-OVERVIEW.md` §14 |
| 要给 Agent 加轨迹记录 / 做 RSI 前置规划 | [`ai/ai-trajectory.md`](./ai/ai-trajectory.md) |

## 全部文档

### 根目录：入口级（3 篇）

| 文档 | 内容 |
| --- | --- |
| [`PROJECT-OVERVIEW.md`](./PROJECT-OVERVIEW.md) | 架构总览：技术栈、运行时拓扑、仓库结构、后端分层与 API 总表、数据模型、AI 子系统（§7）、前端架构、预览导出、开发约束、技术债、术语表、关键决策（§14） |
| [`design-system.md`](./design-system.md) | 设计规范：颜色、字号、圆角、间距、z-index、表单与展示组件约定、存量改造记录 |
| [`本地开发环境启动指南.md`](./本地开发环境启动指南.md) | 两种运行模式的环境准备与启动步骤、常见启动故障排查 |

### `ai/`：AI 子系统专题（4 篇）

| 文档 | 内容 |
| --- | --- |
| [`ai/resume-agent.md`](./ai/resume-agent.md) | 提示词三层分层、内置技能、工具契约、交互与确认卡、如何新增一个技能 |
| [`ai/ai-call-flows.md`](./ai/ai-call-flows.md) | 端到端链路（标注到行号）：一次对话从浏览器到 ai-service 再落库的完整路径 |
| [`ai/ai-chat-pending-change.md`](./ai/ai-chat-pending-change.md) | `ai-call-flows` 的深入篇：待确认变更（pending-change）的数据流、校验与落库时机 |
| [`ai/ai-trajectory.md`](./ai/ai-trajectory.md) | Agent 轨迹落盘格式：存了什么、缺什么，P0 已落地，P1/P2 待定 |

### `architecture/`：架构决策（1 篇）

| 文档 | 内容 |
| --- | --- |
| [`architecture/pi-agent重构设计.md`](./architecture/pi-agent重构设计.md) | pi-agent 重构的动机、目标架构、迁移路径。`status: accepted`，同时作为旧链路退役后的回滚依据 |

> 5 个 ADR（Node 旁路服务、统一对话入口、MySQL 事实源、section 级工具、模型目录归属）已合并进 `PROJECT-OVERVIEW.md` §14「关键架构决策」，包含各自被拒方案与理由。

### `design/`：外部设计参考（1 篇）

| 文档 | 内容 |
| --- | --- |
| [`design/workbuddy-visual-reference.md`](./design/workbuddy-visual-reference.md) | WorkBuddy 桌面端界面拆解（v2）：四层 token 架构（含 VS Code 基色桥）、窗口层窗口配置、色板与字体、尺度与动效、布局夹紧常量、骨架屏、页面骨架、组件配方、交互逻辑、技术栈，以及在本仓库落地的四步路径与风险 |

### `data/`：数据契约（1 篇）

| 文档 | 内容 |
| --- | --- |
| [`data/resume-schema.md`](./data/resume-schema.md) | 简历 JSON 契约、字段表、原子信息（信息库）模型。**字段变更时同步改这里和 `backend/app/core/resume_schema.py`** |

### `learning/`：学习笔记（7 篇，非现状依据）

面试向与技术复盘笔记，**不是当前实现的说明**。

- `01`~`04` 写于 LangChain 时代（2026-08），其中 `02`、`04` 分析的 `chains.py` 等文件已从仓库删除，仅作技术复盘参考；文首均有历史标注。
- `05`~`07` 是 pi-agent 时代的新文（2026-09），内容仍有效。

### `reviews/`：代码审查（1 篇）

| 文档 | 内容 |
| --- | --- |
| [`reviews/2026-11-16-code-review.md`](./reviews/2026-11-16-code-review.md) | 全仓审查（backend / frontend / ai-service）：P0 伪造 XFF 绕过限频、PDF 导出 SSRF、后台设置存储型 XSS、积分并发扣减、私有图片可被共享缓存转发等问题的证据、影响与修复建议；末尾附三批修复进度 |

### `archive/`：已归档（1 篇）

| 文档 | 归档原因 |
| --- | --- |
| [`archive/pi-agent重构验证清单.md`](./archive/pi-agent重构验证清单.md) | 一次性验收清单，对应票据已全部完成 |

## 维护约定

- **单一事实源**：架构与功能现状只写在 `PROJECT-OVERVIEW.md`；子系统细节写各自专题文档并在总览里留一句指引，不要在总览里复制细节。
- **新增文档**：放到对应主题目录，并**回来更新本索引**——没有进索引的文档等于不存在。
- **失效即改**：重命名或移动文档时，用 `grep -rn "<旧文件名>" --include="*.md" .` 找出全部引用一并修正。
- **行号会漂移**：`ai/ai-call-flows.md` 与 `ai/ai-chat-pending-change.md` 标注了代码行号，基准为 2026-09-04，引用时以符号名（函数/类名）为准。
