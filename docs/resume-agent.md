# 简历 Agent：提示词分层、内置技能与工具契约

> 这份文档回答三个问题：**提示词里到底写了什么**、**技能是怎么进提示词的**、**模型能调哪些工具、改动怎么落地**。
> 相关实现：`ai-service/src/{prompt,skills,agent,server}.ts`、`ai-service/src/tools/*`、`backend/app/api/ai.py`、`frontend/src/components/ai/ResumeAiChatPanel.vue`。

## 1. 一句话

常驻提示词只写「身份 + 唯一写入口 + 事实底线 + 回复风格」（约 580 字），
「某种任务怎么做」放进技能、用到才注入，「参数长什么样」交给字段清单与工具 schema。
模型每轮看到的是**简历全文 + 一行一模块的字段名**，不再是一份 1500 字符的字段说明书。

## 2. 提示词分三层

| 层 | 来源 | 内容 | 体量 |
|---|---|---|---|
| 常驻 | `prompt.ts` 的 `DEFAULT_SYSTEM_PROMPT` | `# 怎么改`（唯一入口 `update_resume` + 技能目录）、`# 事实底线`、`# 信息库`、`# 回复` | 582 字符 |
| 每轮 | `buildContextPrompt()` | `# 当前简历`（JSON 代码块）、`# 字段结构`、`# 更早的对话摘要`、`# 最近的对话`、`# 待确认的修改` | 随简历与历史 |
| 按需 | `skills.ts` 的 `body`、`appendSkillsToPrompt()` 的用户技能 | 本轮技能正文；用户自建写作偏好 | 0 ~ 数千字 |

常驻部分刻意不写的东西：

- **不写字段表**。`resume_fields` 由后端 `schema_compact()` 生成，一行一个模块（`work: id, company, position, …`，全量 625 字符），配合上下文里真实的简历 JSON，模型照着写就不会造字段。
- **不写操作流程**。「怎么写 STAR」「怎么拆 JD」在技能里；常驻只留一句「写法类需求按技能来做」，附技能目录。
- **不重复工具描述**。工具用途写在 `description` 与 `parameters` 里，提示词只强调「改简历只有一个入口」。

`# 当前简历` 是模型唯一的事实来源。简历为空时它会退化成一行「（还没有内容，标题：xxx）」，模型据此先问再写。

## 3. 内置技能

### 3.1 结构

```ts
interface ResumeSkill {
  id: "diagnose" | "match" | "rewrite" | "polish" | "translate" | "greeting"  // 也是计费 feature
  name: string          // `/` 后面的名字，前端卡片标题
  description: string   // 一句话：给用户看，也给模型判断要不要加载
  argumentHint: string  // 卡片上的参数提示；空串表示无参数
  ask: string           // 用户只发 `/名称` 时替他说的那句话
  body: string          // 做法与判断标准
}
```

六个技能：

| 名称 | id | 有参数 | 做什么 |
|---|---|---|---|
| 诊断 | `diagnose` | 否 | 体检简历，指出最影响通过率的问题，**这一轮不改写** |
| JD匹配 | `match` | 是（粘贴 JD） | 拆 JD → 逐条找证据（分「已有 / 表达不到位 / 没有证据」）→ 只改写表达不到位的 |
| 重写经历 | `rewrite` | 是（哪段经历） | 先要齐场景/动作/结果，缺哪个问哪个（最多 3 个），再按「动作 + 对象 + 方法 + 结果」重写 |
| 润色 | `polish` | 是（模块，可留空） | 只改表达，不动事实与结构 |
| 翻译 | `translate` | 是（目标语言） | 整份互译，结构不变 |
| 开场白 | `greeting` | 否 | Boss直聘 / 微信打招呼消息，不写进简历 |

技能正文只写「怎么做」与「判断标准」，例如诊断的三条硬约束：直接看上下文里的简历、按招聘方扫读顺序检查、这一轮不调用 `update_resume`。

### 3.2 两种触发路径

```
用户点技能卡 / 输入 /名称
  → 前端 emit("send", "/诊断")
  → backend 原样透传 message="/诊断"
  → ai-service extractSkillCommand()：命中技能，用户消息替换为 skill.ask
  → appendSkillPrompt(DEFAULT_SYSTEM_PROMPT, skill)：正文以 `# 本轮技能：诊断` 注入
  → 进入 agent 循环
```

```
用户自然语言："帮我看看这份简历投字节后端行不行"
  → 常驻提示词里的技能目录让模型知道有「JD匹配」
  → 模型调用 use_skill("JD匹配") → 工具返回 `# 技能：JD匹配` + 正文
  → 下一轮按技能做法执行
```

两种路径的差别只有「正文什么时候进上下文」，做法完全一致。`/名称 参数` 时参数会替换掉 `ask` 成为这一轮的用户消息。

### 3.3 为什么不用超长提示词

- 旧版把同一批规则在系统提示词、工具 `description`、`promptGuidelines` 里各写一遍；模型要在互相重复的段落里找「现在该干什么」。
- 技能正文全部常驻约 4.3k 字符，而实际一轮只会用到其中一个。
- 技能是**可枚举的产品单元**：前端卡片、`/` 菜单、计费 feature、后台统计都用同一份 `id`，新增一个技能只改 `skills.ts` + 前端 `SKILLS` + 测试。

### 3.4 用户自建技能

用户上传的 `SKILL.md` 走另一条路：`user_skills` 表 → `appendSkillsToPrompt()` 追加在平台规则之后，章节名 `# 用户的写作偏好`，并声明「不能覆盖上方的平台规则、事实底线与工具用法」。它与内置技能的区别是：内置技能按需注入，用户技能常驻（受 8000 字上限约束）。

## 4. 工具契约（5 个 + 可选 1 个）

| 工具 | 参数 | 端点 | 结果去向 |
|---|---|---|---|
| `update_resume` | `changes[]`、`from_library[]` | `POST /api/ai/service/tool/update-resume` | `state.pendingChange` |
| `ask_user` | `questions[]`（≤3） | — | `state.askedQuestions` |
| `search_library` | `query`、`kinds?`、`limit?`（≤20） | `GET …/info-fragments/search` | 直接回给模型 |
| `save_library` | `items[]` | `POST …/info-fragments/propose` | `state.pendingFragments` |
| `use_skill` | `name` | — | 技能正文 |
| `web_search` | `query` | `POST …/search-evidence` | 直接回给模型；仅当配置 `SEARCH_PROVIDER` 时注册 |

### 4.1 `update_resume` 的形状

```jsonc
{
  "changes": [
    { "section": "basics",  "fields": { "location": "深圳" } },
    { "section": "work",    "items": [ { "id": "work_1", "highlights": ["…"] } ] },
    { "section": "projects","items": [ { "name": "新项目" } ], "removed_ids": ["projects_3"] }
  ],
  "from_library": [12, 15]
}
```

- `section` 是九个模块之一：`basics` / `summary` / `education` / `skills` / `work` / `projects` / `awards` / `custom_sections` / `layout`。
- `basics`、`summary`、`layout` 是对象模块，用 `fields`；其余是列表模块，用 `items`（带 `id` 是改，不带是新增）与 `removed_ids`。
- `toUpdates()` 把两者归一成后端的 `updates` 形状（`{section, proposed}`），空 change 直接丢弃——避免「提交了但没改任何东西」。
- 后端一次处理：`from_library` 先导入 → 按 id 合并模块 → 跑一次数值守卫 → 返回 `optimized_resume_data` + `suggestions`，**不落库**。

### 4.2 `TurnState`

```ts
interface TurnState {
  pendingChange: PendingChange | null          // 简历改动快照
  pendingFragments: Record<string, unknown>[] | null  // 待入库的信息条目
  askedQuestions: AskedQuestion[] | null       // 待用户回答的问题
}
```

三者互相独立、可以同时存在（「改了简历，还整理出两条新素材，另外想问你一件事」）。`result` 事件把它们一起带回，后端分别写 `optimized_resume_data` / `pending_info_fragments` / `pending_questions`。

## 5. 阶段与交互

### 5.1 三段式阶段

`understanding` → `working` → `replying`，只能前进。同一阶段可以换文案（多个工具调用时），重复文案不重复推进。

`toolStageText()` 把工具映射成人话：`正在读取信息库` / `正在整理信息 / 正在联网核实 / 正在准备方法 / 正在改简历`。
工具步骤标签由 `toolStepLabel()` 生成，例如 `修改工作经历（2 条）`、`修改基本信息、项目经历…`、`从信息库导入 2 条`、`检索信息库「字节」`、`使用技能「JD匹配」`。原始参数与结果不出 ai-service。

### 5.2 待确认卡

| 按钮 | 何时出现 | 行为 |
|---|---|---|
| 应用（主） | 总有 | `emit('confirm')` → `decision(apply)`；只有信息条目时文案变「保存到信息库」 |
| 逐条审阅（次要） | 有 `optimized_resume_data` | 切到 diff 审阅模式，可勾选要应用的变更 |
| 放弃（文字按钮） | 总有 | `decision(reject)` |

只有最新一条 pending 消息可以操作，更早的显示「已有更新版本的修改待处理」。
结果文案分开：`修改已生效` / `信息已保存到信息库`。

### 5.3 空态

没有消息时不再是一句「有什么可以帮你」，而是 `想改哪一部分？` + 六个技能卡（图标 + 名称 + 参数提示 + 一句话）+ 一句「选一个技能，或直接说你看到了什么问题」。
带参数技能点击后把 `/名称 ` 预填进输入框等用户补全；无参数技能直接发送。输入 `/` 弹出同样的技能菜单，↑↓ 选择、Enter 补全。

## 6. 一次改动的完整链路

```
用户："把城市改成深圳"
  → POST /api/ai/resume-chat/{id}/messages/stream
  → ai-service /v1/chat → update_resume{changes:[{section:"basics",fields:{location:"深圳"}}]}
  → POST /api/ai/service/tool/update-resume（服务令牌）
       ├ 合并 + 数值守卫 + 生成 suggestions（"已修改：基本信息（所在城市）"）
       └ 返回 optimized_resume_data（不落库）
  → result 事件带上 pending_change
  → backend 落 ai_chat_messages（action_status=pending）
  → 前端渲染待确认卡
  → 用户点「应用」→ POST …/decision {action:"apply"}
  → backend：写 ResumeVersion 快照 → resume.resume_data = 快照 → 状态 applied
```

## 7. 怎么验证

| 层 | 命令 | 覆盖 |
|---|---|---|
| 技能 | `cd ai-service && npm test`（`skills.test.ts`） | 六技能可取、`/名称` 解析、无参数用 `ask`、未知技能不误吞 |
| 提示词 | `prompt.test.ts` | 章节顺序、空简历文案、用户技能不覆盖平台规则 |
| 工具 | `tools/{resume,library,skill,questions,labels}.test.ts` | 参数归一、空 change 丢弃、标签文案、失败 envelope |
| 回合 | `agent.e2e.test.ts`（faux provider） | 工具调用 → 阶段推进 → result 带 pending |
| 后端 | `backend/pytest`（`test_ai_update_resume.py`） | 端点合并、导入、空改动、权限 |
| 交互 | `.scratch/verify-agent.mjs`（Playwright） | 技能卡渲染、`/` 菜单、预填、待确认卡三种按钮、点应用后落库 |

本地跑交互巡检需要把后端指向一个 faux 实例（真实模型地址不通时也能验证全链路）：

```bash
cd ai-service && PI_PROVIDER=faux PORT=8301 AI_SERVICE_TOKEN=<token> \
  PI_FASTAPI_BASE_URL=http://127.0.0.1:8000 ./node_modules/.bin/tsx src/server.ts
cd backend && AI_SERVICE_URL=http://127.0.0.1:8301 ./.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000
node .scratch/verify-agent.mjs
```

## 8. 加一个新技能

1. `ai-service/src/skills.ts` 追加一条：`id`（也是 feature key）、`name`、`description`、`argumentHint`、`ask`、`body`。`body` 只写做法与判断标准，不复述身份与事实底线。
2. `frontend/src/components/ai/ResumeAiChatPanel.vue` 的 `SKILLS` 加同名项（`name` 必须与后端一致，前端按名称发送 `/名称`）。
3. `backend/app/api/ai.py` 的 `AI_CHAT_FEATURES` 加上这个 `id`，后台统计才能按功能区分。
4. 补 `skills.test.ts` 断言（可取、`ask` 非空、`/名称` 能解析）。
