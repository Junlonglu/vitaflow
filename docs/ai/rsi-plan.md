# RSI 规划：先把"能诊断"做出来

> 依据：*When, Where, and How Experience Becomes Structure: Mechanisms for Recursive Self-Improvement in LLM Agents*（Dai, Ruan, Zhang 等，中科院自动化所 / 美团，综述，42 页；40 项代表性研究、20 个更新闭环的逐项对比）
> 与 `ai-trajectory.md` 的分工：那份定"存什么"，这份定"怎么用"。
> 状态：规划稿（2026-11-17），R0 尚未动工。
>
> 引用的章节号 / 图表号按 2026-11-17 拿到的那版 PDF；综述在 OpenReview 上会改版，编号可能位移。七阶段名称、四组件分类、20 个闭环对比这三件事是稳定内容，不依赖编号。

## 0. 一句话结论

**现在还不是 RSI 阶段。** 七阶段里我们只有采集，判定口径错，归因证据没落库，抽象 / 验证 / 保留三步全空。第一件该做的事不是改提示词，是**让失败变得可诊断**——归因没有输入时，任何改动都在猜组件（本文档第一版就猜错过一次，见 §2.1）。

反面参照：论文 Figure 5 统计了 20 个 update loop，Diagnose 与 Abstract 大量标为 NR（未报告），Verify 常被"打分选优"吞掉。学界大多数"自我改进"实质上只有打分 + 筛选，这正是我们当前的样子。

## 1. 论文给的四个判据

### 1.1 四组件按"运行期怎么被使用"分类，不按存储形式

| 组件 | 保留对象 | 作用方式 |
|---|---|---|
| Prompt | 指令、示例 | 直接注入上下文 |
| Memory | 可寻址的经验记录 | 按条件检索 |
| Skill | 可被选择执行的过程 | 调用与组合 |
| Harness | 运行时结构、接口、校验逻辑 | 编排与治理 |

同一句话，注入上下文是 Prompt，按条件检索是 Memory，能被调用是 Skill，写成校验代码是 Harness。**所以"该改哪个组件"是可以判定的**，不是感觉。论文 §3.2 给的五个比较维度里，对我们最有用的两个是 **enforcement**（靠模型自觉还是靠系统强制）和 **governance burden**（验证与维护成本）。

### 1.2 七阶段，且 Evaluate ≠ Verify

Collect → Evaluate → Diagnose → Abstract → Update → Verify → Consolidate。

Evaluate 判断**已发生的执行**，为改动提供证据；Verify 检验**候选改动**。两者混淆的后果是：拿"改进后在新样本上跑得还行"当成"改动有效"，而新样本本来就是选出这个改动的依据。

### 1.3 递归的硬标准

论文区分三种"保留物"：改了当前答案 / 留下可复用结构 / 改了产生改进的流程本身。**只有第三种算递归自我改进**。按这个标准，我们现在连"留下可复用结构"都没闭环——先别谈第二层。

### 1.4 三组负结果（比方法更有用）

- **SWE-Skills-Bench**：多数 Skill 不改变通过率，部分还增加 token 消耗。
- **Harness 演化 vs 同等预算的替代方案**：在 Terminal-Bench 设定下，Harness 演化没有稳定胜过"并行采样 + 顺序精修"，迁移有限。
- **HarnessDev**：后期修订带来的收益"并不总是稳定或可迁移"。

所以判据不是"改完看起来更聪明"，而是**在匹配预算下，改动后的版本是否胜过"不改但多跑几次"**。

## 2. 现状核对（7 条真实轨迹）

7 条来自 2026-11-17 的本地真实链路：真 FastAPI、真校验器、真轨迹落库（模型侧 1 条 faux、1 条 deepseek，其余无工具调用）。不是回放、不是构造数据。

### 2.1 失败调用

轨迹 1 与 2 各有 3 次 `update_resume` 失败，全部 `category=validation`，共 6 次。参数摘要：

| 轨迹 | 调用序 | args_digest |
|---|---|---|
| 1 | 1 | `sections=["basics"], items=0, fields=1, args_chars=61` |
| 1 | 2 | 与上一次**完全相同** |
| 1 | 3 | `sections=["basics"], items=1, fields=0, args_chars=74` |
| 2 | 1 | `sections=["basics"], items=0, fields=1, args_chars=61` |
| 2 | 2 | 与上一次**完全相同** |
| 2 | 3 | `sections=["basics"], items=0, fields=2, args_chars=73` |

三点判断：

1. **形状是合法的**。`changes:[{section:"basics", fields:{…}}]` 正是对象模块的契约，`args_chars=61` 说明只改了一个字段，不是数组/对象搞混。
2. **重试没有信息增量**。第 1、2 次参数完全一致，第 3 次才换形状。这不算"学习"，是模型在盲试。
3. **原因推不出来，而且第一次推错了。** 最初按"basics 有字段白名单"推成"字段名不合法"——实测不成立。`SECTION_ITEM_ALLOWED_FIELDS` 只覆盖列表模块（education / skills / work / projects / awards / custom_sections），**basics 没有白名单**，未知字段会被静默并入。把 `validate_propose_change()` 单独跑一遍，能对 basics 产生 `category=validation` 的路径只剩**空提案**（空值字符串能穿过 `toUpdates`，纯空对象穿不过）；字段白名单只对列表模块生效（最后一行是反例对照）：

| 传参 | 结果 | category |
|---|---|---|
| `basics` + `{"company_name":"字节"}` | 通过（未知字段静默并入） | — |
| `basics` + `{"name":"李四"}` | 通过 | — |
| `basics` + `{"name":""}` | 到后端，拦下：未提供需要修改的模块内容 | validation |
| `basics` + `{}` | `toUpdates()` 阶段就丢弃 → 本地回"没有需要写入的内容" | **无（category 缺失）** |
| `basics` + `items:[{"id":"basics_1"}]` | 到后端，拦下：未提供需要修改的模块内容 | validation |
| `work` + `company_name` | 到后端，拦下：第 1 条记录包含不支持的字段 | validation |

注意第 4 行：`tools/resume.ts` 的 `toUpdates()` 会把空 `fields` 直接丢掉，返回 `details:{ok:false}` 而**不带 category**，轨迹里就记为 `category:null`。这类失败在统计时会被漏掉——补 `reason_code` 时要一并覆盖本地短路分支，不能只映射后端的 `reason`。

所以 6 次失败最可能是**提案为空**（空值，或只给 id 不给内容），不是字段名不合法。`args_chars=61` 与 `{"changes":[{"section":"basics","fields":{"phone":""}}]}` 这类形状吻合；要确证只差把 `reason` 落一次库——这正是 R0 的理由。

顺带一个分类问题：`tool_issue_category()` 只把"量化结果""没有生成实际内容变化"归 `business`，"未提供需要修改的模块内容"落在 `validation`。空提案重试没有意义，属于 `business`；现在它既污染了失败样本池，也让模型以为"改改参数就能过"。

### 2.2 三处硬伤

**① 归因文本被自己丢掉了，不是上游没给。** 后端 `POST /api/ai/service/tool/update-resume` 返回 `{"ok":false,"category":"validation","reason":"<issue 原文>"}`，`reason` 一直有值；`tools/resume.ts` 也把它读进了 `details.reason`。丢在最后一步：`trace.ts` 的 `toolResult` 只接 `reasonChars`，`agent.ts` 传的是 `event.isError ? payloadLength(result) : 0` —— 业务校验失败走的是 `details.ok===false` 而不是 `isError`，所以 6 次失败 **`reason_chars` 全是 0**，`reason` 字段在轨迹里根本不存在（不是存了 null）。

所以补齐不需要改后端协议、也不需要新开上游字段：**把已经拿到手的 `details.reason` 归一成 `reason_code` 落库即可**。

**② 回合判定口径错位。** `agent.ts` 里 `outcome:"failed"` 只在"模型不可用 / 流式中断"时写入；工具失败不影响它。7 条轨迹 `outcome` 全是 `ok`，包括那两条工具全失败的回合。`ai-trajectory.md` §7 写的"outcome = ChatTurnOutcome.failed + toolQuality 失败项"与实现不一致，需要按实现纠正或按文档补救——建议后者。

**③ 版本归因其实已经有了。** `prompt_versions.rules = r393e428342`、`schema = sa8cab775e9` 已随 `turn_start` 落库，分段 hash（rules / schema / skill）是现成的。缺的不是版本归因，是**改的能力**和**验证的样本**（没有 replay 集）。改的能力也比想象中近：`server.ts:672` 已支持用请求体里的 `system_prompt` 整份覆盖默认提示词，只是 `ai_service_client.py` 的 `ai_service_chat_events()` 有这个参数、**唯一调用点没传**——外置的管道埋好了，差一根线。真正的常量只有 `taskPrompts.ts`（生成 / 导入 / 摘要三条任务提示词）。

结论：顺序上"让失败可诊断"排在"提示词外置"之前。

## 3. 七阶段落点

| 阶段 | 现在有什么 | 补什么 | 落点 | 验收 |
|---|---|---|---|---|
| Collect | 事件流完整（`ai_agent_traces`） | 按 skill × `task_success` 分桶抽样（`outcome` 全是 ok，不能当分桶键） | `backend/scripts/export_traces.py` 加 `--bucket` | 能一条命令导出"某技能的全部失败回合" |
| Evaluate | 工具 `ok` 是硬信号 | 回合级 `task_success`：工具全失败=失败；出现 apply=成功；其余 unknown | `agent.ts` 收尾、`trace.ts` 的 `end()` | 6 次失败回合不再被记成 ok |
| Diagnose | **无**（只有 `reason_chars=0`） | `tool_result` 存 `reason_code` + 出错字段路径；枚举至少含 `empty_proposal` / `unknown_field` / `type_mismatch` / `id_not_found` / `no_effective_change`（本地 `toUpdates` 丢弃）；来源是后端已有的 `details.reason` **加本地分支**，不改后端协议 | `tools/resume.ts`（映射）+ `trace.ts`（写入） | 同类失败能按 `reason_code` 直接聚类 |
| Abstract | **无** | 离线把重复失败归纳成"契约不匹配"类，人工确认 | 新脚本，先不自动生成生产文案 | 能从 30 条失败里自动列出 Top 3 类 |
| Update | rules hash 可归因 | 提示词外置为带版本的文件；校验错误文案改写。**先接已有的 `system_prompt` 覆盖口**（`ai_service_chat_events()` 有这个参数，`ai_chat_service.py:707` 唯一调用点没传），再谈把 `taskPrompts.ts` 搬出去 | `taskPrompts.ts`、`server.ts:672` | 改一句规则不发版，可回滚 |
| Verify | **无** | 固定 replay 集（每 skill 30–50 条），选择集与测试集**不重叠** | 新脚本 + 一张种子表 | 同一个补丁在测试集上分数可复现（同版本同模型） |
| Consolidate | **无** | 版本台账：hash / 变更摘要 / 选择集分数 / 回滚指针 | 轻表或 jsonl，先不建大表 | 任一版本可一键回滚并复测 |

## 4. 第一个实验：`update_resume` 的 6 次失败

挑它的理由：**唯一的硬信号**（`ok=false` 是事实，不需要人打分），样本自产（每次失败自动进池），占比高（7 次工具调用里 6 次失败，唯一成功的是 `query_chars=0` 的 `search_library`）。

### 4.1 先取证，再选干预

§2.1 的实测把根因收窄到两条互斥假设，修法完全不同：

| 假设 | 判别证据 | 干预 | 组件 |
|---|---|---|---|
| 模型想**清空**字段（`{"phone":""}`），被 `_section_update_has_content` 当成"没内容" | `reason` 落库 + 原参数值 | 允许显式清空：白名单字段收 `null`/`""` | Harness |
| 模型**没想清楚**给什么（只给 id、给空对象） | 同上 | 错误文案带"有效改动的形状"+ 正例；`tool_issue_category` 改判 business | Harness + Prompt |

两条假设共用同一个前置动作：**把已经拿到的 `details.reason` 归一成 `reason_code` 落库（R0）**。没有它，后面的补丁只能靠读代码猜——本文档第一版就是这么猜错的，证据链在 §2.1。

### 4.2 候选干预

| 组件 | 干预 | 强制力 | 维护成本 |
|---|---|---|---|
| Harness | 空提案前置拦截，错误里直接给"有效形状"示例（现在只回"未提供需要修改的模块内容"，模型无从纠正） | 高（模型绕不过） | 低 |
| Harness | `tool_issue_category` 把空提案归 `business`（只改样本口径，不改行为） | 高 | 一行 |
| Prompt | 工具描述补"空值等于没改"与 basics 允许字段清单 + 一个正例 | 低（靠自觉） | 低 |
| Skill | 规则里加"改 basics 前先读字段清单" | 低 | 中（每个技能都要维护） |

论文 §5 / Figure 6 的 API 迁移例子给的原则是：**保留作用域最小的那个改动**。这里作用域最小的不是"新建 basics 白名单"（它现在根本没有，加约束可能误伤合法的部分字段更新），而是**错误文案 + 分类**——一行字符串，改错也只影响提示。

注意：论文 §8.2 对 AutoHarness 的提醒同样适用——**合法率上升不等于任务成功率上升**。如果用户本来就想清空、我们只是让模型学会"换个说法"，成功率不会动。

### 4.3 验收口径

1. 新样本上的 `reason_code` 分布变化**而不是失败率下降**——空提案改判 `business` 后失败率会假降，必须看分布和 apply 率；
2. **同一批样本上，其他技能的 `task_success` 不下降**（这是 Verify，不是 Evaluate）；
3. 平均步数不上升（防止"多试几次总能成"被当成改进）；
4. 与基线对照：不改动、同预算多采样 3 次的版本，作为对照臂。

## 5. 评估设计

论文 Table 7 给了五种对照，我们逐条对应：

| 问题 | 对照 | 我们的现状 |
|---|---|---|
| 改动有效吗 | 同批任务改动前后 | 缺基线，做不了 |
| 是哪个组件起作用 | 有/无该组件 | 缺，需要 replay 集 |
| 迁移到新任务吗 | 新任务 / 换模型 | 缺 |
| 多次迭代是否更好 | 有/无保留经验的后续迭代 | 缺，这是递归判据 |
| 改进方法本身变好吗 | 同起点不同方法版本 | 缺 |

三条纪律：

1. **选择集与测试集不重叠。** 用同一批样本选出补丁再用它证明补丁有效，是自我确认。论文里 SkillOpt 专门留了测试集，Self-Harness 也承认"held-out 分数仍参与了选择"，所以它不是完全独立的最终测试。
2. **基线要匹配预算。** 对照臂 = 不改资产、但允许同样的采样次数。否则分不清收益来自"更好的资产"还是"更多的搜索"。
3. **成本要计入。** 跑 replay 集本身消耗 token 和点数，必须单独记账（`ai_tasks` 里可以用一个专用 `task_type` 打标），不能混进用户侧成本。

## 6. 分期

期号用 `R` 前缀，与 `ai-trajectory.md` 的落盘分期（P0 / P1' / P1 / P2）区分开：**本文 R0 = 那份文档的 P1'**。

| 期 | 内容 | 依赖 | 完成标志 |
|---|---|---|---|
| R0 | `details.reason` → `reason_code` 落库；`turn_end` 补 `task_success`；`tool_issue_category` 把空提案改判 `business` | 无 | 新产生的失败能按原因聚类 |
| R1 | replay 集 + 选择/测试集切分 + 导出脚本按 `task_success` 分桶 | R0 攒够样本 | 能对任一技能跑出一份可复现的分数 |
| R2 | 首个补丁（错误文案 / 清空语义）+ Verify | R1 | `reason_code` 分布改善、其他技能不退化、步数不涨 |
| R3 | 退役机制（leave-one-out：去掉某条规则看分数是否下降） | R2 累积 3 轮以上 | 有规则被退役的记录 |

R0 只加一处映射和两个字段，但它是后面所有步骤的前提——**没有它，R2 的补丁只能靠读代码猜**。本文档第一版已经演示过一次猜错的代价（§2.1）。

## 7. 不做什么

1. **不让 agent 改代码**（校验逻辑、工具实现、路由）。简历质量没有自动判分，改完只能人肉回归，出事就是 P0 级事故。
2. **不盲目加 skill**。SWE-Skills-Bench 的负结果就是冲这个来的：多数 Skill 不改变通过率，部分只增加 token。
3. **样本不足不迭代。** 分两件事：30–50 条即可建 replay 集（它是**测量工具**，不是训练集）；但改动资产要等每技能攒到 100 条以上，否则 score 的波动大于补丁的收益。样本不足时 Verify 只能是 NR，这不是"可以跳过的一步"。
4. **不把 apply 率当唯一目标**。用户没看见待确认卡、直接关页面，都会被记成 reject，这个指标系统性偏低。

## 8. 安全与合规

- **L2 原文仍然默认关闭**（见 `ai-trajectory.md` §4）。`reason_code` 必须设计成结构化的枚举 + 字段路径，不含字段值和简历内容，才能进 L1 永久留存。
- **不可信输入会进入 Prompt**。简历文本由用户提供，其中的指令性内容可能影响模型行为。目前靠系统提示词里的边界声明，属于"软约束"；真要防，得在 Harness 层加输出侧校验（待评估，不在本期）。
- **任何改动都要有回滚路径**。Consolidate 阶段的版本台账不是可选项——论文 §10.5 把"编辑历史 + 回滚方法"列为可复现性的必要条件。
