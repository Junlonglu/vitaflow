# VitaFlow — LangChain & LangGraph 深度分析

> 面试向文档：AI 子系统的设计、实现与工程决策

> **历史文档（2026-08 撰写，技术已退役）**：本文分析的 LangChain / LangGraph 实现已随旧链路于 2026-09 全部移除，`requirements.txt` 中已无相关依赖，`backend/app/services/ai/` 仅剩 `token_usage.py`。文中接口路径、类名、调用链**均不再对应现有代码**，请勿据此修改代码。现行实现由 `ai-service/`（pi-agent 运行时）承载，决策背景见 `docs/adr/0001~0005`。保留本文仅作技术复盘与演进参考。

---

## 1. AI 子系统全局架构

```
backend/app/services/ai/
├── llm.py          ← LLM 工厂（ChatOpenAI 实例化）
├── prompts.py      ← 10+ 提示词模板（ChatPromptTemplate）
├── chains.py       ← LCEL 链 + JSON 解析/修复/归一化 + 流式
├── graphs.py       ← LangGraph StateGraph（JD 优化多步流水线）
└── schemas.py      ← Pydantic 输出契约（7 个模型）

backend/app/services/
└── ai_chat_service.py  ← 多轮对话 + Pending/Apply/Reject 状态机

backend/app/api/
└── ai.py               ← 15 个 AI 端点（同步 + SSE 流式）
```

### 数据流总览

```
用户请求
  → FastAPI 端点（Pydantic 入参校验）
    → chain 函数（_invoke_json / stream_json_events）
      → ChatPromptTemplate.invoke()（填充模板变量）
        → ChatOpenAI（LLM API 调用）
          → LLM 返回字符串
            → _parse_json_content（多层 JSON 提取）
              → 失败则 _repair_json_content（二次 LLM 修复）
            → _normalize_ai_payload（schema 感知的结构归一化）
              → schema.model_validate（Pydantic 校验）
                → 失败则再修一次（schema-aware LLM 修复）
              → 返回 Pydantic 模型实例
    → success(data) 包装响应
```

---

## 2. LLM 工厂（llm.py）

```python
def get_llm() -> ChatOpenAI:
    if not settings.ai_api_key:
        raise AppException("请先在 .env 中配置 AI_API_KEY")
    return ChatOpenAI(
        api_key=settings.ai_api_key,
        base_url=settings.ai_base_url,    # 默认 DeepSeek
        model=settings.ai_model,           # 默认 deepseek-chat
        temperature=settings.ai_temperature,  # 默认 0.7
        request_timeout=settings.ai_timeout,  # 默认 60s
    )
```

### 设计要点

**面试回答：为什么每次调用都 `get_llm()` 创建新实例，不做单例缓存？**

1. `ChatOpenAI` 构造很轻量，只是存配置，真正的 HTTP 连接在 `.invoke()` / `.stream()` 时才建立
2. 如果做单例缓存，`settings` 热更新时（如切换模型）会拿到旧配置
3. LangChain 内部已有 HTTP 连接池复用，不需要我们在外层再加缓存

**Provider 可替换性**：只需改 `.env` 的 `AI_BASE_URL` + `AI_API_KEY` + `AI_MODEL`，就能从 DeepSeek 切换到 OpenAI / 智谱 / 月之暗面等任何 OpenAI 兼容 API。`ChatOpenAI` 类名虽带 OpenAI，但 `base_url` 参数让它兼容所有 OpenAI 协议的服务。

---

## 3. 提示词工程（prompts.py）

### 架构模式

所有提示词都用 `ChatPromptTemplate.from_messages()` 定义，system + human 双角色：

```python
SCORE_RESUME_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "你是一位严格但务实的中文简历评估专家...{JSON_RULES}"),
    ("human", "请评估以下简历：{input_json}"),
])
```

变量 `{input_json}` 在 `.invoke()` 时填充，保证模板和数据的分离。

### 共享规则块

**`JSON_RULES`**（7 条，几乎所有提示词都引用）：
1. 只输出合法 JSON，不要 markdown 代码围栏
2. 英文双引号，无尾逗号，无注释，必须闭合
3. 不得编造公司/学校/证书/项目
4. 可以优化措辞/结构/关键词/动词/结果表达
5. 中文，正式，简洁，ATS 友好
6. 必须使用指定的顶层字段名，不要 wrapper 对象
7. 内容过长时缩减描述，不要破坏 JSON 结构

**`RESUME_DATA_SCHEMA_RULES`**：定义 `resume_data` 的精确 schema（每个 section 的必需字段），确保 LLM 输出结构对齐。

### 关键提示词设计

#### 评分提示词 — 防泄露

```
不要评估或提及 JSON、字段命名、数据结构、schema、内部字段、接口格式
```

**为什么**：LLM 会把"使用了 JSON 格式""字段命名规范"当成简历优点来夸。这是 **prompt leakage**——用户看到"你的简历 JSON 结构清晰"会很困惑。评分应该只评价简历内容本身。

#### 聊天提示词 — 最复杂的行为控制

```
- 只有用户明确要求修改时才生成 optimized_resume_data
- 不得编造学校/公司/项目/日期/量化结果
- 新数字必须已存在于简历或对话中
- 信息不足时必须先追问，不要直接生成
- 但用户说"你来生成/帮我创建/不用问我"时视为授权
- reply 和 suggestions 必须自然中文，永远不显示 JSON/schema/字段名
```

这是**行为约束**，不是内容约束。核心思路：LLM 是"聪明的工具"，但必须限制它"太主动"。

---

## 4. 链式调用与 JSON 防御（chains.py）

这是整个 AI 子系统最核心、代码量最大的文件。核心解决一个问题：**LLM 输出不可信，如何防御性解析？**

### 4.1 三层 JSON 修复策略

```
LLM 输出字符串
    ↓
第一层：_decode_json_candidate（本地修复，无 LLM 调用）
  ├─ 去除 markdown 代码围栏（```json ... ```）
  ├─ 去除尾逗号（,} 和 ,]）
  ├─ 从第一个 { 或 [ 开始提取（跳过 LLM 前缀文字）
  ├─ 用 raw_decode() 容忍尾部垃圾
  └─ 多候选组合：6 种尝试路径
    ↓（全部失败）
第二层：_repair_json_content（LLM 修复 JSON 语法错误）
  ├─ 用 JSON_REPAIR_PROMPT 让 LLM 修复
  ├─ 截断到 12000 字符防 token 溢出
  └─ 修复结果再走 _parse_json_content
    ↓（JSON 解析成功但 Pydantic 校验失败）
第三层：schema-aware LLM 修复
  ├─ 把 Pydantic 校验错误 + 原始 JSON 一起给 LLM
  ├─ 让 LLM 按错误信息修复结构
  └─ 修复结果再归一化 + 再校验
    ↓（仍失败）
抛出 AppException
```

**面试回答：为什么要三层？**

- 第一层（本地修复）覆盖 90% 的情况：尾逗号、代码围栏、前缀文字。零延迟。
- 第二层覆盖 8%：LLM 输出了截断的 JSON、嵌套错误。需要一次额外 LLM 调用。
- 第三层覆盖 1%：JSON 合法但 schema 不对（如字段名拼错、类型错误）。需要 LLM 理解校验错误并修复。

三层加起来覆盖 99%+ 的 LLM 输出异常，用户体验是"几乎不会看到 AI 报错"。

### 4.2 Schema 感知归一化（_normalize_ai_payload）

这是最长最关键的函数。**问题**：LLM 经常不按 schema 输出，但"大致对"，不应该直接丢弃。

**策略**：每个 Pydantic schema 对应一套归一化规则：

| 问题 | LLM 常见错误 | 归一化策略 |
|------|-------------|-----------|
| 包装层 | `{"result": {"resume_data": ...}}` | 逐层剥开 `result`/`data`/`message` |
| 键别名 | 用"得分"代替"score"、"建议"代替"suggestions" | 多候选键查找 |
| 嵌套错误 | `{"resume_data": {"resume_data": ...}}` | 检测双重嵌套，取内层 |
| 类型错误 | 评分 0.85（小数）vs 85（整数） | `_safe_int()` 自动 ×100 |
| 结构变异 | summary 是字符串 vs `{"content": "..."}` | `_stringify_summary()` 统一 |
| 数组位置 | skills 在 `optimized_skills` / `items` / `data` | `_list_value()` 多路径提取 |

**面试回答：为什么不直接让 Pydantic 报错然后 LLM 重试？**

1. 重试一次 LLM 调用 = 2-5 秒延迟 + 额外 token 消耗
2. 归一化是确定性的、零延迟的、不消耗 token
3. 归一化处理的是"结构正确但格式偏差"的问题，LLM 重试也不一定能修对
4. 归一化 + Pydantic 校验的组合：先修已知问题，再让 Pydantic 检查剩余问题

### 4.3 流式输出（stream_json_events）

```python
async def stream_json_events(prompt, payload, schema):
    yield {"type": "start"}
    chunks = []
    async for chunk in (prompt | get_llm()).astream(payload):
        text = _chunk_text(chunk)
        chunks.append(text)
        yield {"type": "delta", "text": text}
    
    # 流式结束后，用完整文本做校验
    full_text = "".join(chunks)
    data = _validate_json_content(full_text, schema, payload)
    yield {"type": "result", "data": data.model_dump()}
```

**关键设计**：流式输出的是"原始 token"，但最终 `result` 是经过完整校验的。前端可以先展示 delta（打字效果），但实际数据以 result 为准。

### 4.4 文本清洗

- **`_clean_score_text()`**：正则删除评分中的"JSON 格式""字段命名"等 schema 泄露文本
- **`_localize_field_names()`**：30+ 字段名映射（basics → 基本信息），按 key 长度降序匹配防部分匹配，用正则词边界防误替换
- **`_extract_labeled_facts()`**：解析"姓名：张三\n电话：138..."格式的用户输入，自动映射到 resume_data 字段

---

## 5. LangGraph：JD 匹配优化流水线

### 为什么需要 LangGraph？

**面试回答**：

JD 匹配优化是一个**多步骤推理任务**，需要：
1. 先理解 JD 提取关键词
2. 再分析简历与 JD 的差距
3. 然后逐模块优化（skills → projects → work → summary）
4. 最后评分

如果用单次 LLM 调用做这些事：
- 输出太长，JSON 容易截断
- 每个模块的优化质量下降（注意力分散）
- 无法复用中间结果

LangGraph 让每个步骤成为一个独立节点，每步专注一件事，状态在节点间传递累积。

### State 定义

```python
class JdOptimizeState(TypedDict, total=False):
    resume_data: dict            # 原始简历
    job_description: str         # JD 文本
    job_keywords: dict           # 节点1产出：提取的关键词
    match_analysis: dict         # 节点2产出：匹配度分析
    optimized_resume_data: dict  # 节点3-6逐步修改：优化后的简历
    suggestions: list[str]       # 节点7产出：优化建议
    score: int                   # 节点7产出：匹配评分
```

`total=False` 表示所有键可选——因为每个节点只返回部分键，LangGraph 自动合并到 state 中。

### 8 节点线性流水线

```
START
  → analyze_jd          提取岗位关键词
  → analyze_resume      分析简历-JD 匹配度
  → optimize_skills     优化技能模块
  → optimize_projects   优化项目模块
  → optimize_work       优化工作经历模块
  → optimize_summary    优化个人简介
  → score_resume        评分 + 建议
  → build_result        终端节点（透传 state）
→ END
```

### 每个节点的实现模式

```python
def optimize_skills_node(state: JdOptimizeState) -> dict:
    data = deepcopy(state.get("optimized_resume_data", state.get("resume_data", {})))
    result = _node_json("只优化 skills 模块...", state)
    data["skills"] = _list_value(result, "skills", data.get("skills", []))
    return {"optimized_resume_data": data}
```

**关键设计**：
1. **deepcopy**：每个节点在副本上修改，不污染原始 state
2. **增量修改**：`optimized_resume_data` 从原始简历的深拷贝开始，每个节点只替换自己负责的 section
3. **全 state 输入**：`_node_json` 把整个 state 序列化为 JSON 传给 LLM，所以每个节点都能看到前面所有节点的产出
4. **统一提示词**：`JD_NODE_PROMPT` 只有一个，通过 `{task}` 变量区分不同节点的任务描述——复用模板，减少维护成本

### 为什么是线性而不是条件分支？

```
可能的改进：如果没有 summary，跳过 optimize_summary 节点
选择不这么做的原因：
1. 线性流水线更简单、更可预测
2. 即使 summary 为空，LLM 也可以生成一个
3. 条件分支增加调试难度，而线性流程的每一步结果都可以检查
```

### 与单次调用的对比

| | LangGraph 多节点 | 单次 LLM 调用 |
|---|---|---|
| 质量 | 每步专注，质量高 | 一锅炖，各模块质量不均 |
| 稳定性 | 每步输出短，JSON 不易截断 | 输出过长，经常截断 |
| 可调试 | 可检查每个节点的中间状态 | 只有最终输出，黑盒 |
| 延迟 | 7 次 LLM 调用，总 15-30s | 1 次调用，3-8s |
| Token 消耗 | 较多（每次传完整 state） | 较少 |

**选择 LangGraph 的理由**：JD 匹配优化是"重质量轻速度"的场景——用户主动触发，愿意等 15-30s 获得更精准的结果。

---

## 6. AI 对话系统：Pending/Apply/Reject 状态机

这是整个项目行为最复杂的模块。

### 核心设计原则

**AI 不能直接修改简历。** 所有 AI 提出的修改必须经过用户确认才生效。

### 状态机

```
用户发消息
    ↓
resume_chat_chain() → 结构化结果
    ↓
有 optimized_resume_data？
  ├─ 无 → action_status = "none"，直接展示回复
  └─ 有 → action_status = "pending"
      → 前端展示变更预览 + "应用/拒绝"按钮
          ↓
      用户确认 → action_status = "applied"
        → 创建 ResumeVersion 快照（可回退）
        → 合并 optimized_resume_data 到当前简历
      或
      用户拒绝 → action_status = "rejected"
        → 不修改简历
```

### 流式对话的特殊处理

流式对话的流程更复杂，因为它**还要处理确认/拒绝**：

```python
async def stream_chat_message(db, user, resume, content):
    # Phase 1: 检测是否是确认/拒绝指令
    decision = _decision_from_text(content)
    if decision:
        pending = _latest_pending_change(...)
        if pending:
            resolve_chat_change(db, user, resume, message_id, decision)
            # 流式生成确认/拒绝的回复
            async for event in resume_chat_action_reply_stream(...):
                yield event
            return
    
    # Phase 2: 正常对话
    yield {"type": "phase", "phase": "preparing_changes", "text": "正在分析简历和你的要求"}
    result = resume_chat_chain(payload)  # 先拿到结构化结果（权威）
    
    # 校验、归一化...
    
    yield {"type": "phase", "phase": "replying", "text": "正在组织回复"}
    # 流式生成回复文本，传入 prepared_result 约束流式 LLM
    async for event in resume_chat_reply_stream(payload_with_result):
        yield event
    
    yield {"type": "result", "data": ...}
```

**关键设计**：**先出结构化结果，再出流式文本**。流式 LLM 收到 `prepared_result` 作为输入，被提示词约束"必须严格遵循 prepared_result"。这防止了流式回复"说改了但实际没改"的矛盾。

### 防幻觉机制

#### 1. 数字声明验证

```python
def _unverified_numeric_claims(payload, optimized_data):
    """检查 AI 优化的简历中是否包含来源中不存在的量化数字"""
    source_numbers = _numeric_claims(payload.resume_data) 
                      | _numeric_claims(payload.history)
                      | _numeric_claims(payload.user_message)
    optimized_numbers = _numeric_claims(optimized_data)
    return optimized_numbers - source_numbers  # 多出来的 = 编造的
```

**例子**：简历中没有"提升 30%"，AI 在优化后写入了"提升 30%"→ 被检测到 → 阻止写入，回复"请提供具体的提升数据"。

#### 2. 空变更检测

```python
def _validated_optimized_data(payload, current_data, optimized_data):
    merged = _merge_confirmed_resume_data(current_data, optimized_data)
    if merged == current_data:
        return None, "模型没有生成实际内容变化"  # 阻止无意义写入
```

#### 3. Section 重排的规则引擎

```python
def _prepare_section_reorder(resume_data, content):
    """用规则引擎而非 LLM 处理模块排序——排序是确定性操作，不需要 AI 创造力"""
    # 解析"把教育经历移到项目经历前面"→ 确定性修改 section_order
    # 比让 LLM 做 更可靠、更快、零 token 消耗
```

### 意图分类关键词

| 类别 | 示例 | 处理 |
|------|------|------|
| 修改请求 | "帮我改"、"优化"、"润色"、"移到" | 触发 optimized_resume_data 生成 |
| 确认指令 | "确认"、"同意"、"可以"、"按这个修改" | 应用 pending 变更 |
| 拒绝指令 | "取消"、"不要修改"、"不改了" | 拒绝 pending 变更 |
| 授权生成 | "你来生成"、"帮我创建"、"不用问我" | AI 可以生成缺失内容 |
| 追问标记 | "请告诉我"、"需要补充" | AI 在追问而非修改 |

---

## 7. Pydantic 输出契约（schemas.py）

7 个模型，三重作用：

1. **LLM 输出校验**：`schema.model_validate(data)` 确保结构正确
2. **API 响应序列化**：`.model_dump()` 生成干净字典
3. **提示词文档**：schema 结构被引用在提示词中，指导 LLM 输出格式

| Schema | 字段 | 用途 |
|--------|------|------|
| `ResumeGenerateResult` | resume_data, template_id, template_config, explanation | AI 生成完整简历 |
| `ResumeScoreResult` | score, level, summary, details, strengths, weaknesses, suggestions | 7 维度评分 |
| `SectionOptimizeResult` | optimized_section, changes, suggestions | 单模块优化 |
| `ProjectOptimizeResult` | optimized_project, changes, suggestions | 项目优化 |
| `ResumeOptimizeResult` | optimized_resume_data, summary, changes, suggestions | 全简历优化 |
| `JdOptimizeResult` | job_keywords, match_analysis, optimized_resume_data, score, suggestions | JD 匹配优化 |
| `ResumeChatResult` | reply, suggestions, optimized_resume_data（可选） | 多轮对话 |

**`ResumeChatResult.optimized_resume_data` 设为 `Optional[dict] = None`**：当用户只是咨询不修改时，不返回优化数据。这是"是否产生变更"的信号。

---

## 8. API 端点设计

### 同步 vs 流式双端点模式

每个核心功能都有两个端点：

```python
# 同步版
@router.post("/generate-resume")
async def generate_resume(body: ResumeGenerateIn, ...):
    result = generate_resume_chain(payload)
    return success(result.model_dump())

# 流式版
@router.post("/generate-resume/stream")
async def generate_resume_stream(body: ResumeGenerateIn, ...):
    return stream_events(generate_resume_stream(payload))
```

**流式 SSE 格式**：`application/x-ndjson`（Newline-Delimited JSON）

```json
{"type": "start"}
{"type": "delta", "text": "```"}
{"type": "delta", "text": "json"}
{"type": "delta", "text": "\n{\"basics\":"}
...
{"type": "result", "data": {"resume_data": {...}, "template_id": "tech"}}
```

### LangGraph 端点的特殊性

```python
# LangGraph 版（不支持流式）
@router.post("/optimize-by-jd")
async def optimize_by_jd(body: JdOptimizeIn, ...):
    result = optimize_by_jd_graph(resume_data, job_description)
    return success(result.model_dump())

# 流式版用的是单次调用，不是 LangGraph
@router.post("/optimize-by-jd/stream")
async def optimize_by_jd_stream(body: JdOptimizeIn, ...):
    return stream_events(optimize_by_jd_stream(payload))
```

**为什么 LangGraph 不支持流式？** 因为 `.invoke()` 是同步调用，7 个节点串行执行，无法在中间 yield。如果要流式，需要在每个节点完成时 yield 一个 phase 事件，这需要用 LangGraph 的流式 API（`.astream_events()`），当前未实现。

---

## 9. 工程决策总结

| 决策 | 选择 | 替代方案 | 理由 |
|------|------|---------|------|
| LLM 调用 | 每次新建实例 | 单例缓存 | 配置热更新、LangChain 内部已有连接池 |
| JSON 修复 | 三层策略（本地→LLM→schema-aware） | 失败直接重试 | 覆盖 99%+ 异常，最小化 token 消耗 |
| 输出归一化 | schema 感知的确定性修复 | 全部让 LLM 重试 | 零延迟、零 token、确定性 |
| JD 优化 | LangGraph 8 节点线性图 | 单次 LLM 调用 | 质量优先，每步专注一件事 |
| AI 变更 | Pending/Apply/Reject | AI 直接写入 | 用户控制权，防误操作 |
| 流式对话 | 先结构化结果→再流式文本 | 纯流式 | 流式文本受结构化结果约束，防止矛盾 |
| 模块排序 | 规则引擎 | 让 LLM 做 | 确定性操作不需要 AI，更快更可靠 |
| 数字验证 | 声明来源比对 | 信任 LLM | 防止量化结果幻觉 |
| Prompt 复用 | JD_NODE_PROMPT + {task} 变量 | 每节点独立 prompt | 减少维护成本，统一规则 |
