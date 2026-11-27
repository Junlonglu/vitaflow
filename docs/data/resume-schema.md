# 简历 JSON 契约与原子信息模型

> 单一事实来源：`backend/app/core/resume_schema.py`。本文是该模块的说明文档，字段表变更时同步改这里，并递增 `SCHEMA_VERSION`。

## 1. 为什么需要一份显式契约

简历一直是 JSON，但形状过去只隐含在「渲染模板 + 起手式样例 + 各模块的 normalize」里：

- 同一份 section 字段表在 `resume_normalization.py`、`ai_chat_service.py` 的合并逻辑、Node 工具的参数描述里各写一遍，改一个字段要改三处；
- 列表条目没有 id 或 id 重复时，AI 的「按 id 增量更新」会打偏；
- Agent 只能从示例里猜字段名，容易自造字段（`position` vs `role`、`highlights` vs `details`）；
- 前端保存、AI 写回、导入三条路径各自规范化，容易出现「同一份简历两种形状」。

现在把结构声明成数据（`FieldSpec` / `SectionSpec` / `FragmentKindSpec`），由同一个规范化函数收口：

```
                       ┌─ 前端保存 PUT /resumes/{id}
resume_data (JSON) ◀───┼─ AI 写回 resolve_chat_change(apply)
                       └─ 信息库导入 import / apply
        ↓ 全部经过 normalize_resume_document()
   统一形状 + schema_version + 稳定 id
        ↓
   预览 / 导出 / 下一次对话上下文
```

## 2. 顶层结构

```jsonc
{
  "schema_version": "1.1",
  "basics": { "name": "", "title": "", /* … 单人字段 */ "custom_fields": [], "field_config": {} },
  "summary": { "content": "" },
  "education": [{ "id": "edu_1", "school": "", "major": "", "degree": "", "start_date": "", "end_date": "", "description": "" }],
  "skills":    [{ "id": "skill_1", "name": "", "keywords": [], "description": "" }],
  "work":      [{ "id": "work_1", "company": "", "position": "", "start_date": "", "end_date": "", "description": "", "highlights": [] }],
  "projects":  [{ "id": "project_1", "name": "", "role": "", "start_date": "", "end_date": "", "tech_stack": "", "description": "", "highlights": [] }],
  "awards":    [{ "id": "award_1", "name": "", "date": "", "description": "" }],
  "custom_sections": [{ "id": "custom_1", "title": "", "preset_type": "", "items": [{ "title": "", "subtitle": "", "start_date": "", "end_date": "", "description": "", "highlights": [] }] }],
  "layout": { "section_order": [], "hidden_sections": [], "section_titles": {}, "field_labels": {}, "skills_options": {} }
}
```

- 六个列表模块（`education` / `skills` / `work` / `projects` / `awards` / `custom_sections`）的条目都有稳定 `id`，AI 的增量更新按 id 定位，避免整段覆盖。
- `layout` 只由界面维护（顺序、显隐、标题文案），Agent 的契约里明确「不要手改」。
- `custom_sections` 承载证书、语言、竞赛、培训、论文、志愿、实习等「非标准模块」，用 `preset_type` 标记类型，渲染与导入都靠它匹配。

## 3. 模块字段表

类型：`text` 短文本、`long_text` 长文本、`date` 写成 `YYYY.MM`、`list` 字符串数组。

| 模块 | 标题字段 | 字段 |
|---|---|---|
| `basics`（对象） | `name` | `name`*、`title`、`status`、`phone`、`email`、`location`、`expected_salary`、`highest_degree`、`website`、`github`、`avatar`（另有 `custom_fields`、`field_config` 由界面维护） |
| `summary`（对象） | — | `content`（long_text） |
| `education` | `school` | `school`*、`major`、`degree`、`start_date`、`end_date`、`description` |
| `skills` | `name` | `name`*、`keywords`(list)、`description` |
| `work` | `company` | `company`*、`position`、`start_date`、`end_date`、`description`、`highlights`(list) |
| `projects` | `name` | `name`*、`role`、`start_date`、`end_date`、`tech_stack`、`description`、`highlights`(list) |
| `awards` | `name` | `name`*、`date`、`description` |
| `custom_sections` | `title` | `title`*、`preset_type`、`items`（条目固定字段：`title`、`subtitle`、`start_date`、`end_date`、`description`、`highlights`、`tech_stack`） |

`*` 为必填（指 AI 新建条目时应提供，数据库层不强制）。文本上限：短文本 400、长文本 4000、简介 4000；标题类字段在生成 id 前缀时使用 `edu_ / skill_ / work_ / project_ / award_ / custom_ / citem_`。

> 工作经历的岗位字段是 **`position`**，项目经历的角色字段是 **`role`** —— 这一对最容易写反，Agent 契约里已显式列出。
>
> `basics.avatar` 是**账号头像库的引用**，不是简历内容：只存对象名（`avatar/{user_id}_{uuid}.jpg`），渲染时由 `avatar_service` 拼成存储代理地址；旧数据里的完整 URL 仍然兼容。头像不随 JSON 导出——导出时剥掉本系统引用，导入时残留引用会被清空并提示用户到信息库重新选。

## 4. 写入前的规范化（`normalize_resume_document`）

对任意来源的数据：

1. **补 custom_sections 的 id**：必须早于 `normalize_resume_data`，否则缺 id 的模块会被排到 `section_order` 之外（等于凭空隐藏）。
2. 调用原有的 `normalize_resume_data` 补齐基础默认值（兼容历史简历）。
3. 写入 `schema_version`。
4. 按字段表清洗：去首尾空白、统一换行、剔除控制字符、截断到 `max_length`；`list` 字段接受字符串（按 `\n；;` 切分）或数组，去空去重保序，最多 40 条，自动去掉行首 `- • ·`。
5. 列表条目补 id：缺失或重复时按 `{prefix}_{n}` 生成，保证同一模块内唯一。
6. **未知字段原样保留**：旧简历的历史字段不会被升级吞掉，`custom_fields`、模板私有字段也能继续存在。

## 5. 给 Agent 的写入契约

Agent 每轮拿到两样东西，都不需要它自己推断字段：

- `resume.data`：简历全文 JSON，模型照现有形状改；
- `resume_fields`：`schema_compact()` 把字段表压成「一行一模块」——

```
basics: name, title, phone, email, location, …
work: id, company, position, start_date, end_date, description, highlights
projects: id, name, role, …
layout: section_order, hidden_sections, section_titles, field_labels
```

`prompt.ts` 把它拼成 `# 字段结构` 一节（排在简历 JSON 之前，稳定内容先入缓存），并说明「新增条目只用这些字段名，结构照简历 JSON 写」。条目 id 的规则在第 3 条。

规则由 `schema_prompt()` 维护（同一份字段表的人读版本，也用于文档与调试）：

1. 日期统一 `"YYYY.MM"`，未知留空；
2. `highlights` / `keywords` 是字符串数组，一条一个要点，不要塞进 `description`；
3. 改已有条目必须带原 id，新增条目不要自带 id（系统生成）；
4. 只用列出的字段，自造字段会被校验拦截；
5. **所有写入都走 `update_resume`**（`changes[]` 声明模块，`from_library[]` 导入信息库），不再有按模块区分的多个写工具。

`schema_json()` 是同一份数据的机器可读版本（含 `fragment_kinds`），供调试与后续编辑器校验使用。

> 为什么从整份契约改成一行一模块：契约正文约 1500 tokens，每轮都付；而简历 JSON 本身已经给出了字段的真实形状。压缩成字段清单后，模型仍然不会自造字段，但每轮省下的上下文可以用在历史与简历内容上。

> Node 侧不复制字段表：ai-service 只负责「把模型意图翻译成工具调用」，字段合法性由 `/api/ai/service/tool/update-resume` 判定。

## 6. 原子信息（信息库）

原子信息 = **一条可复用的事实**，属于用户而不是某份简历。它复用简历条目的字段，因此「导入简历」几乎是字段级平移。

```jsonc
{
  "kind": "internship",           // 分类，决定落到哪个模块、用哪套字段
  "title": "字节跳动 后端实习",     // 由 title_field 推导，可手改
  "summary": "服务端开发；QPS 提升 3 倍",  // 列表视图用的摘要
  "payload": { "company": "字节跳动", "position": "后端开发", "highlights": ["QPS 提升 3 倍"] },
  "tags": ["后端", "实习"],
  "source": "manual",             // manual | resume | ai
  "fingerprint": "…",             // sha1(kind + payload)；basic 用「姓名 + 邮箱/手机」身份指纹
  "is_default": false,            // 仅 basic：默认个人信息，每用户一条
  "usage_count": 2                // 被导入简历的次数
}
```

### 6.1 分类与落点

| kind | 标签 | 落到简历 | 标题字段 | preset_type |
|---|---|---|---|---|
| `basic` | 个人信息 | `basics`（空格填、模板示例值可覆盖） | `name` | — |
| `education` | 教育经历 | `education` | `school` | — |
| `work` | 工作经历 | `work` | `company` | — |
| `internship` | 实习经历 | `custom_sections` | `company` | `internships` |
| `project` | 项目经历 | `projects` | `name` | — |
| `skill` | 专业技能 | `skills` | `name` | — |
| `award` | 荣誉奖项 | `awards` | `name` | — |
| `certificate` | 证书认证 | `custom_sections` | `name` | `certifications` |
| `language` | 语言能力 | `custom_sections` | `name` | `languages` |
| `training` | 培训经历 | `custom_sections` | `name` | `training` |
| `competition` | 竞赛经历 | `custom_sections` | `name` | `competitions` |
| `campus` | 校园经历 | `custom_sections` | `organization` | `campus` |
| `publication` | 论文专利 | `custom_sections` | `title` | `publications` |
| `volunteer` | 志愿服务 | `custom_sections` | `organization` | `social_practice` |
| `custom` | 自定义 | `custom_sections` | `title` | `custom` |

内置模块（教育/工作/项目/技能/荣誉）直接追加到对应数组；其余分类落到 `custom_sections`，按 `preset_type` 复用已有模块，没有就新建（`custom_N`），条目 id 用 `citem_N`，避免与模块 id 撞名。

### 6.2 校验与幂等

- `normalize_fragment_payload(kind, payload)`：按分类字段表清洗；`basic` 只要求「至少一个非空字段」，其余分类要求标题字段非空且至少一个非空字段（「一等奖」本身就是完整的一条信息）。
- 指纹 `sha1(kind + payload)`：同一用户重复保存同一条内容会**合并标题与标签**，不覆盖用户手动编辑过的字段。`basic` 例外，指纹只取身份字段（姓名 + 邮箱，缺邮箱用手机）——`title` / `status` / `expected_salary` 是求职意向，换方向就会变，算进指纹会让同一个人变成两条。
- `basic` 落进简历时：空字段直接填；模板示例值（`Elliot` / `zhangsan@example.com` / `13800000000`，判定在 `services/resume_placeholder.py`）在 `overwrite_placeholders` 打开时被替换；用户自己填过的值不动。
- 导入简历时忽略条目 `id` 判重：已存在的条目不会被重复追加，全部已存在时返回 400「这些信息在简历里已经存在」。
- 所有查询与写入都带 `user_id`；`fragment_id` 指向他人数据时按 404 处理。
- 上限：每用户 500 条、单次批量 30 条、一次提案 10 条、一次导入 20 条、标签 8 个。

### 6.3 两条使用通道

| 通道 | 入口 | 是否直接落库 |
|---|---|---|
| 信息库页面 | `POST /info-fragments/apply`（点「导入简历」） | 直接写入简历并返回最新 `resume_data` |
| 对话 | 工具 `update_resume` 的 `from_library[]` → `pending_change`；工具 `save_library` → `pending_info_fragments` | 否，等用户点「应用 / 保存到信息库」 |

## 7. 改动指引

- 加字段：改 `resume_schema.py` 的 `FieldSpec`，递增 `SCHEMA_VERSION`，同步更新前端表单、模板（`templates/resume/*`）与 Word 导出；数据库无需迁移（JSON 列）。
- 加原子信息分类：在 `FRAGMENT_KINDS` 新增 `FragmentKindSpec`（含 `section`、`title_field`、`preset_type`），信息库页面会自动出现该分类的筛选项与表单，Agent 契约同步生效。
- 加模块：除了 `SECTION_ORDER`、`SECTIONS`，还要检查 `resume_normalization`、`layout.section_order` 默认值、模板渲染与导出，属于跨层改动，先写 ADR。
- 回归：`backend/tests/test_info_fragments.py::ResumeSchemaTest` 覆盖版本、id、未知字段保留、custom 排序与契约文本。
