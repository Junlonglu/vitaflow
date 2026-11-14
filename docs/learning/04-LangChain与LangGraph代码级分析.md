# VitaFlow — LangChain & LangGraph 代码级深度分析

> 逐文件、逐函数、逐行关键代码解读。重点关注 LangChain/LangGraph 的具体用法、设计决策和工程技巧。

> **历史文档（2026-08 撰写，技术已退役）**：本文逐行解读的 `chains.py`、`graph.py`、Agent 工具链等文件**已从仓库删除**，LangChain / LangGraph 依赖也已移除。文中所有代码引用均无法在现有代码库中定位。现行 AI 实现见 `ai-service/src/`（pi-agent 运行时），设计决策见 `docs/PROJECT-OVERVIEW.md` §14。保留本文仅作技术复盘参考，不可作为代码依据。

---

## 一、文件地图与调用关系

```
API 层  ai.py
          │
          ├── 同步/流式 chain 调用 ──→ chains.py ──→ prompts.py + schemas.py + llm.py
          │
          ├── LangGraph 图调用 ──────→ graphs.py ──→ prompts.py + chains.py(复用解析函数) + llm.py
          │
          └── 对话服务调用 ────────→ ai_chat_service.py ──→ chains.py(两个 chain + 两个 stream)
```

**LangChain 在项目中只用了三个核心组件**：

| 组件 | 引入位置 | 作用 |
|------|---------|------|
| `ChatOpenAI` | `llm.py:1` | LLM 调用抽象（兼容任何 OpenAI 协议 API） |
| `ChatPromptTemplate` | `prompts.py:1` | 声明式提示词模板（system+human 双角色） |
| LCEL 管道 `\|` | `chains.py:557` 等 | `prompt \| get_llm()` 组成 RunnableSequence |

**LangGraph 只用了一个组件**：

| 组件 | 引入位置 | 作用 |
|------|---------|------|
| `StateGraph` | `graphs.py:4` | 有状态多步图（JD 匹配优化 8 节点流水线） |

没有用 LangChain 的 Agent、Tool、Retriever、Memory、OutputParser 等高级组件。**项目选择了最小依赖、最大控制权的策略**——只用 LangChain 做 LLM 调用 + 模板 + 管道，其余（JSON 解析、输出校验、状态管理、流式控制）全部自研。

---

## 二、llm.py — LLM 工厂

```python
# llm.py 完整代码（仅 17 行）
from langchain_openai import ChatOpenAI
from app.core.config import settings
from app.core.exceptions import AppException

def get_llm() -> ChatOpenAI:
    if not settings.ai_api_key:
        raise AppException("未配置大模型 API KEY，请在 .env 中配置 AI_API_KEY")
    return ChatOpenAI(
        api_key=settings.ai_api_key,
        base_url=settings.ai_base_url,      # 默认 https://api.deepseek.com/v1
        model=settings.ai_model,             # 默认 deepseek-chat
        temperature=settings.ai_temperature, # 默认 0.7
        timeout=settings.ai_timeout,         # 默认 60s
    )
```

### 关键设计：为什么每次调用都 `get_llm()` 新建实例？

1. `ChatOpenAI` 构造极轻——只存 5 个参数，HTTP 连接在 `.invoke()` / `.stream()` 时才建立
2. 避免配置缓存问题：如果做单例，改 `.env` 后旧实例还在用旧 `api_key`
3. LangChain 底层的 `httpx.AsyncClient` 已有连接池复用，不需要外层再加

### Provider 可替换性

`ChatOpenAI` 的 `base_url` 参数让它兼容**所有 OpenAI 协议的 API**：

```
DeepSeek:     base_url=https://api.deepseek.com/v1
OpenAI:       base_url=https://api.openai.com/v1
智谱 GLM:     base_url=https://open.bigmodel.cn/api/paas/v4
月之暗面:      base_url=https://api.moonshot.cn/v1
本地 Ollama:  base_url=http://localhost:11434/v1
```

切换只需改 `.env`，零代码改动。这是 `ChatOpenAI` 相比自写 `requests` 调用最大的价值。

---

## 三、prompts.py — 提示词模板

### 3.1 ChatPromptTemplate 的用法

所有提示词都用同一个模式：`ChatPromptTemplate.from_messages([("system", ...), ("human", ...)])`

```python
# prompts.py:38-49 示例
GENERATE_RESUME_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "你是一名专业的中文技术岗简历顾问..."
     + JSON_RULES                    # ← 共享规则块，字符串拼接
     + RESUME_DATA_SCHEMA_RULES),    # ← 共享 schema 规则
    ("human", "请根据 target_position 和 personal_info 生成...：\n{input_json}"),
])
```

**LangChain 在这里的作用**：
- `{input_json}` 是模板变量，在 `.invoke({"input_json": "..."})` 时被替换
- `.from_messages()` 自动构造 `ChatPromptValue`，调用时转为 `[SystemMessage, HumanMessage]` 列表传给 LLM
- 这比手动拼 f-string 更安全——变量替换是 token 级别的，不会和用户内容冲突

### 3.2 两套共享规则块

**`JSON_RULES`**（7 条，几乎所有提示词都引用，通过 `+ JSON_RULES` 字符串拼接注入）：

```
1. 只输出合法 JSON，不要 Markdown 代码块          ← 防最常见的 LLM 输出格式错误
2. 英文双引号，无尾逗号，无注释，必须闭合          ← 防语法错误
3. 不要编造公司、学校、证书、获奖、项目             ← 防幻觉（核心）
4. 可以优化措辞、结构、关键词、动作动词和结果表达    ← 允许的优化范围
5. 中文，正式，简洁，ATS 友好                      ← 语言和风格约束
6. 必须使用指定的最外层字段名，不要额外包装          ← 防数据结构偏移
7. 内容较长时优先缩短描述，保证 JSON 完整           ← 防截断
```

**`RESUME_DATA_SCHEMA_RULES`**（仅 generate 和 JD 相关提示词引用）：
- 定义 `resume_data` 每个字段的精确结构
- 规定 `field_config` 必须是对象（不是数组），键和 label 必须按指定映射
- 规定生成原则：保留用户事实、不虚构关键经历、可润色表达性内容

### 3.3 提示词设计的关键技巧

#### 技巧 1：防评分泄露（SCORE_RESUME_PROMPT，第 56 行）

```
不要评价或提及 JSON、字段命名、数据结构、schema、内部字段、接口格式，
也不要把"使用 JSON 格式"当作优点或问题。
```

LLM 会把"简历用了 JSON 格式"当作优点来夸。这是 **prompt leakage**——用户看到"你的 JSON 结构很清晰"会一头雾水。这段指令显式禁止 LLM 评价数据结构层面，只评价简历内容本身。

#### 技巧 2：可复用节点提示词（JD_NODE_PROMPT，第 108-120 行）

```python
JD_NODE_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "你是技术岗 JD 匹配分析和简历定制专家..."),
    ("human", "节点任务：{task}\n输入：{input_json}"),  # ← 两个变量
])
```

LangGraph 的 8 个节点共用这一个模板，区别仅在 `{task}` 的值不同。这样：
- 共享的 JSON_RULES 和约束只需维护一处
- 新增节点只需定义 task 文本
- 减少了 8 个几乎相同的提示词

#### 技巧 3：对话提示词的精细行为控制（CHAT_RESUME_PROMPT，第 139-172 行）

这是最长也最复杂的提示词（34 行），核心是**何时该生成 `optimized_resume_data`**：

```
- 用户只是询问/解释/比较/分析 → 不生成 optimized_resume_data
- 用户要求"改/写/删/优化/补充" → 生成 optimized_resume_data
- 用户要求修改但信息不足 → 先追问，optimized_resume_data = null
- 用户说"你来生成/帮我创建/不用问我" → 视为授权，直接生成
- 新增数字必须已存在于简历或对话中 → 防量化幻觉
- 但如果用户明确指定新增某个项目 → 授权生成，未知字段留空
```

这种**分级行为控制**不是靠一个 switch 语句，而是靠提示词中的条件描述让 LLM 自己判断——这是 prompt engineering 的核心技巧：**把业务规则翻译成 LLM 能理解的决策逻辑**。

#### 技巧 4：流式回复受结构化结果约束（CHAT_RESUME_REPLY_PROMPT，第 174-203 行）

```
输入中的 prepared_result 是系统已经生成并校验过的权威结果，必须严格以它为准，
不得另拟一套修改方案。
prepared_result.has_changes=true 时，只说明其中的实际修改并询问是否确认；
prepared_result.has_changes=false 时，不得声称已经生成、写入或调整简历。
```

这解决一个关键问题：流式回复是第二次 LLM 调用（只生成 Markdown 文本），它可能"自由发挥"说出和第一次结构化调用不一致的话。这段约束让流式 LLM **只能复述已经验证的结果**，不能发明新内容。

#### 技巧 5：JSON 修复提示词（JSON_REPAIR_PROMPT，第 220-229 行）

```python
JSON_REPAIR_PROMPT = ChatPromptTemplate.from_messages([
    ("system", "你是 JSON 修复器。请只输出一个合法 JSON 对象，不要输出 Markdown，不要解释，不要改变原始语义。"
               "如果原内容是数组，请根据任务包装成对象。"),
    ("human", "节点任务：{task}\n需要修复的内容：\n{raw_content}"),
])
```

注意 `{task}` 变量——修复时传入原始任务描述（如"修复 ResumeScoreResult 的 JSON 输出"），让 LLM 知道期望的顶层结构是什么。如果 LLM 返回了裸数组 `[{...}]`，修复器知道应该包装成 `{"details": [...]}`。

---

## 四、schemas.py — Pydantic 输出契约

```python
# schemas.py 完整代码（67 行），7 个模型
```

这些模型有三重作用：

### 作用 1：LLM 输出校验

```python
# chains.py:545
return schema.model_validate(data)  # Pydantic 校验 LLM 输出是否符合预期结构
```

### 作用 2：API 响应序列化

```python
# ai.py:66
return success(generate_resume_chain(payload.model_dump()).model_dump())
#                                                ↑ Pydantic 模型 → 干净字典
```

### 作用 3：提示词中引用的结构文档

`RESUME_DATA_SCHEMA_RULES` 描述的结构必须和 `schemas.py` 定义的结构一致——这是人肉保持的契约。

### 关键设计：`ResumeChatResult.optimized_resume_data` 是 `Optional`

```python
# schemas.py:66
class ResumeChatResult(BaseModel):
    reply: str
    suggestions: list[str] = Field(default_factory=list)
    optimized_resume_data: dict[str, Any] | None = None  # ← 关键
```

`None` 表示"这次对话不涉及简历修改"，非 `None` 表示"AI 提出了修改，需要用户确认"。这是**整个 Pending/Apply/Reject 机制的信号源**。

---

## 五、chains.py — LCEL 链 + JSON 防御 + 流式

这是 AI 子系统最核心的文件（665 行）。按功能逐块分析。

### 5.1 LCEL 管道：LangChain 的核心用法

项目中 LangChain 的核心用法就是**管道操作符 `|`**：

```python
# chains.py:557-560
def _invoke_json(prompt, payload: dict[str, Any], schema: type[T]) -> T:
    chain = prompt | get_llm()    # ← LCEL: ChatPromptTemplate | ChatOpenAI → RunnableSequence
    message = chain.invoke({"input_json": json.dumps(payload, ensure_ascii=False)})
    content = getattr(message, "content", message)
    return _validate_json_content(content, schema, payload)
```

**`prompt | get_llm()` 做了什么？**

1. LangChain 的 `|` 操作符调用 `RunnableSequence.__or__`
2. 生成一个 `RunnableSequence(prompt, get_llm())` 对象
3. `.invoke({"input_json": "..."})` 时：
   - 先调 `prompt.invoke({"input_json": "..."})` → 生成 `ChatPromptValue`
   - 再调 `llm.invoke(ChatPromptValue)` → 调用 LLM API → 返回 `AIMessage`
4. `.stream(...)` 同理，但逐 token 返回

**为什么用 LCEL 而不是直接调 LLM？**

- 同一套代码支持 `.invoke()` 同步和 `.stream()` 流式
- 提示词模板和 LLM 实例解耦——换 LLM 只需改 `get_llm()`，换提示词只需改 `prompt`
- 可组合性：未来可以加 `.bind(stop=["```"])` 或 `.with_fallbacks()` 等

### 5.2 三层 JSON 修复：chains.py 的核心价值

LLM 输出不可信。chains.py 用约 400 行代码（占全文 60%）处理这个问题。

#### 第一层：本地多策略提取（`_decode_json_candidate`，第 68-94 行）

```python
def _decode_json_candidate(text: str) -> Any:
    decoder = json.JSONDecoder()
    candidates = [_strip_json_fence(text)]          # 候选 1：去围栏后的全文
    for opener in ("{", "["):
        index = text.find(opener)
        if index >= 0:
            candidates.append(text[index:])           # 候选 2-3：从第一个 {/ [ 开始

    for candidate in candidates:
        for cleaned in (candidate, _remove_trailing_commas(candidate)):
            cleaned = _strip_json_fence(cleaned)     # 每个候选再试去围栏+去尾逗号
            try:
                return json.loads(cleaned)           # 策略 A：标准解析
            except json.JSONDecodeError:
                pass
            try:
                data, _ = decoder.raw_decode(cleaned) # 策略 B：容忍尾部垃圾
                return data
            except json.JSONDecodeError:
                pass
    raise AppException("AI 输出不是合法 JSON")
```

**最多尝试 6 种组合**：3 个候选 × 2 种清洗（原文 / 去尾逗号），每种再试 2 种解析（`json.loads` / `raw_decode`）= 最多 12 次尝试。

**覆盖的 LLM 输出异常**：

| 异常 | 示例 | 被哪个策略处理 |
|------|------|---------------|
| Markdown 围栏 | ` ```json\n{...}\n``` ` | `_strip_json_fence` |
| 尾逗号 | `{"score": 85,}` | `_remove_trailing_commas` |
| 前缀文字 | `好的，以下是结果：\n{...}` | 从第一个 `{` 开始提取 |
| 尾部垃圾 | `{...}\n\n希望对你有帮助！` | `raw_decode` 容忍 |

#### 第二层：LLM 修复 JSON 语法（`_repair_json_content`，第 108-114 行）

```python
def _repair_json_content(content: Any, task: str) -> dict[str, Any]:
    repair_chain = JSON_REPAIR_PROMPT | get_llm()    # ← 又是 LCEL 管道
    repaired = repair_chain.invoke({"task": task, "raw_content": str(content)[:12000]})
    try:
        return _parse_json_content(getattr(repaired, "content", repaired))
    except AppException as exc:
        raise AppException(f"AI 输出 JSON 修复失败：{exc.message}") from exc
```

**注意**：截断到 12000 字符——LLM 返回的损坏 JSON 可能非常长，不截断会超 token 限制。

#### 第三层：schema-aware 修复（`_validate_json_content`，第 538-553 行）

```python
def _validate_json_content(content, schema, payload=None):
    # 阶段 1：解析 JSON
    try:
        parsed = _parse_json_content(content)
    except AppException:
        parsed = _repair_json_content(content, f"修复 {schema.__name__} 的 JSON 输出")  # 第二层

    # 阶段 2：归一化
    data = _normalize_ai_payload(parsed, schema)

    # 阶段 3：Pydantic 校验
    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        # 第三层：把校验错误 + 原始数据一起给 LLM 修
        repair_input = {"validation_error": str(exc), "raw_json": data}
        repaired = _repair_json_content(
            json.dumps(repair_input, ensure_ascii=False),
            f"根据校验错误修复 {schema.__name__} 的 JSON 结构"
        )
        normalized = _normalize_ai_payload(repaired, schema)
        return schema.model_validate(normalized)
```

**完整流程图**：

```
LLM 原始输出
  ↓
_parse_json_content → 失败 → _repair_json_content（第二层：修 JSON 语法）
  ↓ 成功
_normalize_ai_payload（归一化：确定性修结构偏差）
  ↓
schema.model_validate → 失败 → _repair_json_content + _normalize_ai_payload + model_validate（第三层：修 schema）
  ↓ 成功
返回 Pydantic 模型实例
```

### 5.3 归一化层：`_normalize_ai_payload`（第 407-535 行）

这是最长的函数（128 行），按 `schema` 类型分支处理。每个分支都是同样的模式：**处理 LLM 的常见输出偏差**。

以 `ResumeGenerateResult` 分支为例：

```python
if schema is ResumeGenerateResult:
    resume_data = data.get("resume_data")
    # 偏差 1：LLM 用了别的键名
    if not isinstance(resume_data, dict):
        for key in ["optimized_resume_data", "data", "result", "resume"]:
            if isinstance(data.get(key), dict):
                resume_data = data[key]
                break
    # 偏差 2：双重嵌套
    if isinstance(resume_data, dict) and isinstance(resume_data.get("resume_data"), dict):
        resume_data = resume_data["resume_data"]
    # 偏差 3：LLM 直接返回了简历内容，没有包在 resume_data 里
    if "resume_data" not in data and _looks_like_resume_data(data):
        resume_data = data
        data = {"resume_data": resume_data, "template_id": "tech", ...}
    # 偏差 4：template_id 不合法
    if template_id not in {"classic", "tech", "modern", "blue_timeline"}:
        data["template_id"] = "tech"
    # 最终：归一化简历内容
    data["resume_data"] = _normalize_resume_data(data["resume_data"], {})
```

**所有分支处理的偏差类型汇总**：

| 偏差 | 代码位置 | 处理方式 |
|------|---------|---------|
| 包装层 `result`/`data` | 449-451, 502-504 | 剥开包装 |
| 键名别名 | 280-281, 505 | 多候选键查找 |
| 双重嵌套 | 415-416, 440-441, 494-495, 511-512 | 检测并取内层 |
| 整个对象就是数据 | 417-419, 436-437 | 检测 `_looks_like_resume_data` 后包装 |
| 评分是小数 | `_safe_int` 中 `0 < number <= 1` 时 ×100 | 自动转百分比 |
| 评分键是中文 | 280 `"得分"` / `"满分"` | 多候选键 |
| details 是 dict 不是 list | 453-457 | 遍历键值对转为 list |
| summary 是字符串 | 361-364 | 包装为 `{"content": "..."}` |
| section_content vs optimized_section | 519-528 | 重映射键名 |
| resume_data 缺少字段 | `_normalize_resume_data` | 补默认值 |

### 5.4 流式输出（`stream_json_events`，第 578-598 行）

```python
def stream_json_events(prompt, payload, schema):
    yield {"type": "start"}                        # 事件 1：流开始
    chunks = []
    try:
        chain = prompt | get_llm()                 # LCEL 管道
        for chunk in chain.stream({"input_json": ...}):  # ← .stream() 逐 token
            text = _chunk_text(chunk)
            if not text:
                continue
            chunks.append(text)
            yield {"type": "delta", "text": text}   # 事件 2：每个 token
        result = _validate_json_content("".join(chunks), schema, payload)
        yield {"type": "result", "data": result.model_dump()}  # 事件 3：校验后完整结果
    except AppException as exc:
        yield {"type": "error", "message": exc.message}       # 事件 4：错误
```

**关键设计**：流式期间只 yield 原始文本（`delta`），**不做任何校验**。等全部 token 收齐后，用完整文本走一遍 `_validate_json_content`（三层修复 + 归一化 + Pydantic 校验），yield 最终的 `result`。

这意味着：
- `delta` 事件可能包含不完整/错误的 JSON 片段——前端只用于打字效果展示
- `result` 事件才是可信数据——前端用这个更新状态
- 如果最终校验失败，`error` 事件替代 `result`

### 5.5 `_chunk_text` — 处理 LLM 流式输出的多种格式

```python
def _chunk_text(chunk: Any) -> str:
    content = getattr(chunk, "content", chunk)
    if isinstance(content, str):
        return content                               # 格式 1：纯字符串
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))  # 格式 2：OpenAI 结构化输出
            elif item is not None:
                parts.append(str(item))
        return "".join(parts)
    return "" if content is None else str(content)   # 格式 3：兜底
```

不同 LLM provider 的流式返回格式不同。DeepSeek 返回 `AIMessage(content="...")`，OpenAI 结构化输出可能返回 `AIMessage(content=[{"type":"text","text":"..."}])`。`_chunk_text` 统一处理这些差异。

### 5.6 具体链函数：全是 `_invoke_json` 的薄包装

```python
def generate_resume_chain(payload):   return _invoke_json(GENERATE_RESUME_PROMPT, payload, ResumeGenerateResult)
def score_resume_chain(payload):      return _invoke_json(SCORE_RESUME_PROMPT, payload, ResumeScoreResult)
def optimize_section_chain(payload):  return _invoke_json(OPTIMIZE_SECTION_PROMPT, payload, SectionOptimizeResult)
def optimize_resume_chain(payload):   return _invoke_json(OPTIMIZE_RESUME_PROMPT, payload, ResumeOptimizeResult)
def optimize_by_jd_chain(payload):    return _invoke_json(JD_OPTIMIZE_PROMPT, payload, JdOptimizeResult)
def resume_chat_chain(payload):       return _invoke_json(CHAT_RESUME_PROMPT, payload, ResumeChatResult)
```

每个链函数 = 提示词 + schema，通过 `_invoke_json` 统一走「LCEL 管道 → JSON 解析 → 三层修复 → 归一化 → Pydantic 校验」。唯一的例外是 `generate_resume_chain` 多做了一步 `_normalize_resume_data`。

---

## 六、graphs.py — LangGraph StateGraph

### 6.1 State 定义

```python
# graphs.py:14-21
class JdOptimizeState(TypedDict, total=False):
    resume_data: dict[str, Any]            # 原始简历（只读）
    job_description: str                   # JD 文本（只读）
    job_keywords: dict[str, Any]           # 节点 1 产出
    match_analysis: dict[str, Any]         # 节点 2 产出
    optimized_resume_data: dict[str, Any]  # 节点 3-6 增量修改
    suggestions: list[str]                 # 节点 7 产出
    score: int                             # 节点 7 产出
```

**`total=False` 的含义**：所有键都是 Optional。这是 LangGraph 的关键设计——每个节点函数只返回部分键，LangGraph 自动合并到 state 中：

```python
# 例如 optimize_skills_node 只返回 {"optimized_resume_data": data}
# LangGraph 会把这个 dict 合并到完整 state 里，其他键保持不变
```

### 6.2 节点共享函数 `_node_json`

```python
# graphs.py:24-31
def _node_json(task: str, state: JdOptimizeState) -> dict[str, Any]:
    chain = JD_NODE_PROMPT | get_llm()                    # LCEL 管道
    message = chain.invoke({"task": task, "input_json": _json_dumps(state)})  # 整个 state 传给 LLM
    content = getattr(message, "content", message)
    try:
        return _parse_json_content(content)               # 先尝试本地解析
    except Exception:
        return _repair_json_content(content, task)        # 失败则 LLM 修复
```

**关键**：`_json_dumps(state)` 把**整个 state** 序列化为 JSON 传给 LLM。这意味着：
- 每个节点都能看到前面所有节点的产出
- 节点 3（optimize_skills）能看到节点 1 的 `job_keywords` 和节点 2 的 `match_analysis`
- state 越大，token 消耗越多——这是简单性的代价

注意 `_node_json` 只用了**第一层 + 第二层修复**，没有 `_normalize_ai_payload` 和 Pydantic 校验。这比 `_invoke_json` 轻量，但也意味着节点的输出更"原始"——后处理在 `_list_value` / `_dict_value` 等提取函数中完成。

### 6.3 8 个节点的具体实现

#### 节点 1-2：分析（不修改简历）

```python
# graphs.py:78-83
def analyze_jd_node(state):
    return {"job_keywords": _node_json("分析岗位 JD，提取岗位名称、技能关键词、职责关键词、加分项。", state)}

def analyze_resume_node(state):
    return {"match_analysis": _node_json("分析当前简历与 JD 的匹配度，找出缺失关键词和弱项。", state)}
```

这两个节点只产出分析结果，不修改 `optimized_resume_data`。

#### 节点 3-6：逐模块优化（增量修改简历）

所有优化节点遵循同一模式：

```python
# graphs.py:86-90 — 以 skills 为例
def optimize_skills_node(state):
    data = deepcopy(state["optimized_resume_data"])          # ① 在副本上操作
    result = _node_json("只优化 skills 模块...", state)       # ② 调 LLM
    data["skills"] = _list_value(result, "skills", data.get("skills", []))  # ③ 提取并替换
    return {"optimized_resume_data": data}                   # ④ 返回修改后的 state 片段
```

**`_list_value`** 是关键的容错提取函数：

```python
# graphs.py:40-48
def _list_value(result, key, fallback):
    value = result.get(key)                           # 尝试 result.skills
    if not isinstance(value, list):
        value = result.get(f"optimized_{key}")        # 尝试 result.optimized_skills
    if not isinstance(value, list) and isinstance(result.get("optimized_resume_data"), dict):
        value = result["optimized_resume_data"].get(key)  # 尝试 result.optimized_resume_data.skills
    if not isinstance(value, list):
        value = result.get("items") or result.get("data")  # 兜底：result.items / result.data
    return value if isinstance(value, list) else fallback if isinstance(fallback, list) else []
```

4 层候选查找——因为 LLM 可能把数组放在任何位置。

#### 节点 7：评分

```python
# graphs.py:114-116
def score_resume_node(state):
    result = _node_json("给优化后的简历评分，返回 score 和 suggestions。", state)
    return {"score": _safe_int(result.get("score", 0), 0), "suggestions": _string_list(result.get("suggestions", []))}
```

`_safe_int` 处理 LLM 输出评分的各种格式：整数 `85`、小数 `0.85`、字符串 `"85分"`。

#### 节点 8：终端节点

```python
# graphs.py:119-120
def build_result_node(state):
    return state    # 透传，只是为了给图一个 END 前的终端节点
```

### 6.4 图的构建

```python
# graphs.py:123-142
def build_jd_optimize_graph():
    graph = StateGraph(JdOptimizeState)
    # 添加 8 个节点
    graph.add_node("analyze_jd_node", analyze_jd_node)
    graph.add_node("analyze_resume_node", analyze_resume_node)
    graph.add_node("optimize_skills_node", optimize_skills_node)
    graph.add_node("optimize_projects_node", optimize_projects_node)
    graph.add_node("optimize_work_node", optimize_work_node)
    graph.add_node("optimize_summary_node", optimize_summary_node)
    graph.add_node("score_resume_node", score_resume_node)
    graph.add_node("build_result_node", build_result_node)
    # 线性边
    graph.add_edge(START, "analyze_jd_node")
    graph.add_edge("analyze_jd_node", "analyze_resume_node")
    graph.add_edge("analyze_resume_node", "optimize_skills_node")
    graph.add_edge("optimize_skills_node", "optimize_projects_node")
    graph.add_edge("optimize_projects_node", "optimize_work_node")
    graph.add_edge("optimize_work_node", "optimize_summary_node")
    graph.add_edge("optimize_summary_node", "score_resume_node")
    graph.add_edge("score_resume_node", "build_result_node")
    graph.add_edge("build_result_node", END)
    return graph.compile()    # ← 编译为可执行的 CompiledGraph
```

**LangGraph 在这里做的事情**：
1. `StateGraph(JdOptimizeState)` 创建一个有状态图
2. `add_node(name, func)` 注册节点函数
3. `add_edge(from, to)` 定义执行顺序
4. `.compile()` 返回 `CompiledGraph`，可以 `.invoke(state)` 执行

**`.invoke(state)` 时的内部流程**：
1. 初始化 state
2. 按 edge 顺序依次调用节点函数
3. 每个节点返回的 dict 合并到 state（LangGraph 自动做 `state.update(return_value)`）
4. 最终 state 作为 `.invoke()` 的返回值

### 6.5 入口函数 `optimize_by_jd_graph`

```python
# graphs.py:145-163
def optimize_by_jd_graph(resume_data, job_description):
    # 1. 归一化输入
    normalized = _normalize_resume_data(deepcopy(resume_data), {})
    # 2. 初始化 state
    state = {
        "resume_data": normalized,
        "optimized_resume_data": deepcopy(normalized),  # ← 深拷贝作为工作副本
        "job_description": job_description,
        "suggestions": [],
        "score": 0,
    }
    # 3. 执行图
    result = build_jd_optimize_graph().invoke(state)
    # 4. 后处理
    result["job_keywords"] = ... if isinstance(..., dict) else {}
    result["match_analysis"] = ... if isinstance(..., dict) else {}
    result["optimized_resume_data"] = _normalize_resume_data(result.get("optimized_resume_data", {}), {})
    result["score"] = _safe_int(result.get("score", 0), 0)
    result["suggestions"] = _string_list(result.get("suggestions", []))
    # 5. Pydantic 校验
    try:
        return JdOptimizeResult.model_validate(result)
    except ValidationError as exc:
        raise AppException(f"JD 优化结果校验失败：{exc}") from exc
```

**`optimized_resume_data` 的增量修改过程**：

```
初始化: optimized_resume_data = deepcopy(原始简历)
  ↓ optimize_skills_node
optimized_resume_data.skills = [优化后的 skills]
  ↓ optimize_projects_node
optimized_resume_data.projects = [优化后的 projects]   # skills 保留，只替换 projects
  ↓ optimize_work_node
optimized_resume_data.work = [优化后的 work]            # skills + projects 保留
  ↓ optimize_summary_node
optimized_resume_data.summary = {优化后的 summary}      # skills + projects + work 保留
```

每个节点 `deepcopy(state["optimized_resume_data"])` 后只替换自己负责的 key，其他 key 原样保留。这样最终的 `optimized_resume_data` 是**所有模块优化的累积结果**。

### 6.6 LangGraph vs 单次调用的对比（同一 API 的两个实现）

```python
# ai.py:99-101 — LangGraph 版（多节点）
@router.post("/optimize-by-jd")
def optimize_by_jd(payload, ...):
    return success(optimize_by_jd_graph(payload.resume_data, payload.job_description).model_dump())

# ai.py:109-111 — 单次调用版（一个 LLM 调用）
@router.post("/optimize-by-jd-single")
def optimize_by_jd_single(payload, ...):
    return success(optimize_by_jd_chain(payload.model_dump()).model_dump())
```

两个端点功能相同但实现不同，方便对比和测试。

---

## 七、ai_chat_service.py — 对话服务

这个文件不直接使用 LangChain，但调用了 `chains.py` 中的三个函数：`resume_chat_chain`、`resume_chat_reply_stream`、`resume_chat_action_reply_stream`。

### 7.1 对话的两阶段架构

```
用户消息
  ↓
阶段 1：resume_chat_chain() → ResumeChatResult（结构化，权威）
  - 包含 reply / suggestions / optimized_resume_data
  - 经过完整三层修复 + 归一化 + Pydantic 校验
  ↓
阶段 2：resume_chat_reply_stream() → Markdown 文本流（展示用）
  - 输入包含 prepared_result（阶段 1 的结果）
  - 被提示词约束"必须严格遵循 prepared_result"
  - 不能发明阶段 1 中没有的修改
```

**为什么不直接流式输出结构化结果？**

1. 结构化 JSON 不适合直接展示给用户（包含 `resume_data` 等内部数据）
2. 用户看到的是自然语言回复，但实际数据修改由阶段 1 决定
3. 两阶段解耦：阶段 1 可以做校验（数字验证、空变更检测），阶段 2 只负责"翻译"成自然语言

### 7.2 流式对话的分支逻辑（`stream_chat_message`，第 497-626 行）

```python
def stream_chat_message(db, user, resume, content):
    session, message, payload = _chat_context(db, user, resume, content)
    decision = _decision_from_text(message)       # 检测是否确认/拒绝指令

    yield {"type": "start", "data": {"user_message_id": ...}}

    if decision and pending_change:
        # 分支 A：确认/拒绝待定变更
        resolve_chat_change(...)                   # 直接修改简历
        for text in resume_chat_action_reply_stream(...):
            yield {"type": "delta", "text": text}  # 流式生成确认回复
        return

    if decision:
        # 分支 B：确认/拒绝但没有待定变更
        for text in resume_chat_action_reply_stream({"status": "no_pending", ...}):
            yield {"type": "delta", "text": text}
        return

    # 分支 C：正常对话
    yield {"type": "phase", "phase": "preparing_changes", ...}
    structured_result = resume_chat_chain(payload)  # 阶段 1
    # ... 校验、归一化 ...
    yield {"type": "phase", "phase": "replying", ...}
    for text in resume_chat_reply_stream(...):      # 阶段 2
        yield {"type": "delta", "text": text}
    yield {"type": "result", "data": ...}
```

三个分支对应三种用户意图，每种走不同的 LLM 调用路径。

### 7.3 数字声明验证（第 364-409 行）

```python
# 收集所有数字声明
_NUMERIC_CLAIM_RE = re.compile(r"(?<![A-Za-z])\d+(?:\.\d+)?(?:%|\+)?")

def _numeric_claims(value, key=None):
    if key in _NUMERIC_SKIP_KEYS:    # 跳过 id、row、order 等结构性数字
        return set()
    # ... 递归遍历，收集所有匹配 _NUMERIC_CLAIM_RE 的数字 ...
    if isinstance(value, str):
        return set(_NUMERIC_CLAIM_RE.findall(value))

def _unverified_numeric_claims(payload, optimized_data):
    source = {
        "resume_data": payload.get("resume", {}).get("resume_data"),
        "history": payload.get("history"),
        "user_message": payload.get("user_message"),
    }
    return _numeric_claims(optimized_data) - _numeric_claims(source)
    #       ↑ 优化后出现的数字        ↑ 原始来源中没有的数字 = 编造的
```

**例子**：

```
原始简历: "负责后端 API 开发"
AI 优化: "负责后端 API 开发，QPS 提升 30%"    ← "30%" 不在来源中
检测结果: _unverified_numeric_claims 返回 {"30%"}
处理: 阻止写入，回复"请提供真实指标"
```

### 7.4 Section 重排规则引擎（第 273-343 行）

```python
def _prepare_section_reorder(resume_data, content):
    # 1. 如果是疑问句，跳过（"是否应该把教育经历提前？"）
    if any(marker in content for marker in ("是否", "该不该", "要不要")):
        return None

    # 2. 匹配用户消息中的 section 名（支持别名 + 自定义标题）
    aliases = dict(SECTION_ALIASES)
    section_titles = resume_data.get("layout", {}).get("section_titles", {})
    for key, title in section_titles.items():
        aliases[title.strip()] = key

    for label, key in sorted(aliases.items(), key=lambda x: len(x[0]), reverse=True):
        position = content.find(label)
        if position >= 0:
            matches.append((position, key, label))

    # 3. 确定目标位置
    if destination:
        if any(word in destination_tail[:8] for word in ("后面", "之后", "下方")):
            insert_at = destination_index + 1
        else:
            insert_at = destination_index
    elif "最后/末尾" in content:
        insert_at = len(order)
    elif "最前/提前/置顶" in content:
        insert_at = 1

    # 4. 执行排序并生成回复
    order.insert(insert_at, target_key)
    return optimized_data, reply, suggestions
```

**为什么不用 LLM 排序？**

1. 排序是确定性操作，不需要 AI 创造力
2. 规则引擎零延迟零 token，LLM 需要 2-5 秒
3. 规则引擎 100% 可靠，LLM 可能搞错顺序
4. LLM 的排序结果和规则引擎的结果可能冲突——优先使用规则引擎

---

## 八、ai.py — API 端点层

### 8.1 流式响应封装

```python
# ai.py:49-61
def stream_events(events):
    def iterator():
        for event in events:
            yield json.dumps(event, ensure_ascii=False) + "\n"   # NDJSON 格式
    return StreamingResponse(
        iterator(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",    # ← 防止 Nginx 缓冲流式响应
        },
    )
```

### 8.2 同步/流式双端点模式

每个核心功能都有两个端点：

```python
@router.post("/generate-resume")               # 同步
def generate_resume(payload, ...):
    return success(generate_resume_chain(payload.model_dump()).model_dump())

@router.post("/generate-resume/stream")         # 流式
def generate_resume_stream_api(payload, ...):
    return stream_events(generate_resume_stream(payload.model_dump()))
```

注意同步版调用 `chain` 函数，流式版调用 `stream` 函数——它们是 `chains.py` 中两个独立的函数，分别用 `.invoke()` 和 `.stream()` 实现。

### 8.3 LangGraph 端点的特殊性

```python
@router.post("/optimize-by-jd")                 # LangGraph 版（不支持流式）
def optimize_by_jd(payload, ...):
    return success(optimize_by_jd_graph(payload.resume_data, payload.job_description).model_dump())

@router.post("/optimize-by-jd/stream")          # 流式版用的是单次 chain，不是 LangGraph
def optimize_by_jd_stream_api(payload, ...):
    return stream_events(optimize_by_jd_stream(payload.model_dump()))
```

**为什么 LangGraph 不支持流式？**

`build_jd_optimize_graph().invoke(state)` 是同步阻塞调用——7 个节点串行执行，中间无法 yield。如果需要流式，需要用 LangGraph 的 `.astream_events()` API 在每个节点完成时 yield 中间结果。当前未实现。

---

## 九、LangChain/LangGraph 使用总结

### 用了什么

| LangChain 组件 | 用法 | 文件 |
|---------------|------|------|
| `ChatOpenAI` | LLM 调用抽象，兼容 OpenAI 协议 | `llm.py` |
| `ChatPromptTemplate.from_messages` | 声明式提示词模板，支持变量替换 | `prompts.py` |
| LCEL 管道 `\|` | `prompt \| get_llm()` → `RunnableSequence`，支持 `.invoke()` / `.stream()` | `chains.py`, `graphs.py` |

| LangGraph 组件 | 用法 | 文件 |
|---------------|------|------|
| `StateGraph` | 有状态多步图，8 节点线性流水线 | `graphs.py` |
| `TypedDict` + `total=False` | 定义图状态，节点只返回部分键 | `graphs.py` |
| `add_node` / `add_edge` | 注册节点和边 | `graphs.py` |
| `.compile()` / `.invoke()` | 编译和执行图 | `graphs.py` |

### 没用什么（及原因）

| 未使用的组件 | 原因 |
|-------------|------|
| LangChain Agent | 项目不需要工具调用，所有 LLM 交互都是"输入提示词→解析 JSON" |
| LangChain Tool | 同上 |
| LangChain OutputParser | 自研了更强大的三层 JSON 修复 + 归一化，比 PydanticOutputParser 更可靠 |
| LangChain Memory | 对话历史自己存在 MySQL（AiChatMessage），比 LangChain Memory 更可控 |
| LangChain Retriever | 暂无 RAG 需求 |
| LangGraph 条件边 | JD 优化是线性流程，不需要分支 |
| LangGraph 人工审核节点 | Pending/Apply/Reject 机制在 service 层自研实现，不用 LangGraph 的 interrupt |
| LangGraph 流式 `.astream_events()` | 未实现，当前 LangGraph 端点只支持同步 |

### 核心设计原则

1. **最小依赖**：只用 LangChain 3 个组件 + LangGraph 1 个组件，其余自研
2. **最大控制权**：JSON 解析、修复、归一化、流式控制全部自己实现，不依赖 LangChain 的抽象
3. **防御性编程**：LLM 输出不可信，三层修复 + 归一化 + Pydantic 校验确保最终数据正确
4. **用户控制权**：AI 不能直接修改简历，Pending/Apply/Reject + 数字声明验证 + 空变更检测
5. **确定性优先**：排序等确定性操作用规则引擎，不交给 LLM
