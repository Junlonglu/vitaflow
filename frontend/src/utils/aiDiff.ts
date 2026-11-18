export type DiffKind = "added" | "modified" | "removed"

export interface DiffItem {
  id: string
  kind: DiffKind
  sectionKey: string
  entryKey: string
  title: string
  before?: string
  after?: string
}

export interface SectionDiff {
  key: string
  title: string
  changes: DiffItem[]
}

interface ComparableEntry {
  key: string
  title: string
  text: string
  compareText: string
}

/**
 * 简历数据是动态 JSON：整份数据、自定义模块条目、对象型 section 都是「字符串键 → 未知值」。
 * 这些 helper 把 unknown 收窄成可安全访问的结构，替代原来的裸 any——字段是否存在、
 * 是什么类型，由运行时判断，编译器不再假定「什么都有」。
 */
type DataRecord = Record<string, unknown>

function asRecord(value: unknown): DataRecord | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? (value as DataRecord) : null
}

function asArray(value: unknown): unknown[] | null {
  return Array.isArray(value) ? value : null
}

function layoutOf(data: unknown): DataRecord | null {
  return asRecord(asRecord(data)?.layout)
}

function sectionTitlesOf(data: unknown): DataRecord | null {
  return asRecord(layoutOf(data)?.section_titles)
}

function sectionOrderOf(data: unknown): string[] {
  const order = asArray(layoutOf(data)?.section_order) || []
  return order.filter((item): item is string => typeof item === "string" && Boolean(item))
}

function customSectionsOf(data: unknown): unknown[] {
  return asArray(asRecord(data)?.custom_sections) || []
}

function customSectionIdsOf(data: unknown): string[] {
  return customSectionsOf(data)
    .map((item) => asRecord(item)?.id)
    .filter((id): id is string => typeof id === "string" && Boolean(id))
}

const builtInTitles: Record<string, string> = {
  basics: "基本信息",
  summary: "个人简介",
  education: "教育经历",
  skills: "专业技能",
  work: "实习/工作经历",
  projects: "项目经历",
  awards: "荣誉奖项",
}

const ignoredKeys = new Set(["id", "avatar", "field_config", "layout", "custom_fields", "preset_type"])
const fieldOrder = [
  "name",
  "title",
  "phone",
  "email",
  "status",
  "location",
  "highest_degree",
  "website",
  "github",
  "expected_salary",
  "school",
  "major",
  "degree",
  "company",
  "organization",
  "institution",
  "issuer",
  "publisher",
  "platform",
  "position",
  "role",
  "level",
  "score",
  "award",
  "start_date",
  "end_date",
  "date",
  "credential_id",
  "url",
  "tech_stack",
  "research_area",
  "keywords",
  "description",
  "content",
  "highlights",
]
const fieldOrderMap = new Map(fieldOrder.map((key, index) => [key, index]))
const fieldLabels: Record<string, string> = {
  basics: "基本信息",
  summary: "个人简介",
  education: "教育经历",
  skills: "专业技能",
  work: "实习/工作经历",
  projects: "项目经历",
  awards: "荣誉奖项",
  section_type: "模块类型",
  section_title: "模块标题",
  section_content: "模块内容",
  name: "名称",
  title: "标题",
  phone: "电话",
  email: "邮箱",
  status: "当前状态",
  location: "所在城市",
  highest_degree: "最高学历",
  website: "个人网站",
  github: "代码仓库",
  expected_salary: "期望薪资",
  custom_sections: "自定义模块",
  company: "公司",
  organization: "组织",
  institution: "机构",
  issuer: "颁发机构",
  publisher: "期刊/专利号/会议",
  platform: "平台",
  position: "职位",
  role: "角色",
  level: "水平",
  score: "成绩",
  award: "奖项",
  school: "学校",
  major: "专业",
  degree: "学历",
  credential_id: "证书编号",
  url: "链接",
  start_date: "开始时间",
  end_date: "结束时间",
  date: "时间",
  description: "说明",
  content: "内容",
  keywords: "关键词",
  highlights: "亮点",
  tech_stack: "技术栈",
  research_area: "研究领域",
}

function compact(value: unknown): string {
  const text = String(value ?? "")
  if (!/<[a-z][\s\S]*>/i.test(text)) return text.trim()
  const doc = new DOMParser().parseFromString(text, "text/html")
  doc.body.querySelectorAll("br").forEach((node) => node.replaceWith("\n"))
  doc.body.querySelectorAll("p,div,li,blockquote,h1,h2,h3,h4").forEach((node) => node.append("\n"))
  return (doc.body.textContent || "").replace(/\n{3,}/g, "\n\n").trim()
}

function splitTagText(value: unknown) {
  if (Array.isArray(value)) return value.map((item) => compact(item)).filter(Boolean)
  return String(value ?? "")
    .split(/[,，、;；\n\r]+/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function formatFieldValue(key: string, value: unknown) {
  if (key === "keywords" || key === "tech_stack") return splitTagText(value).join(", ")
  return plain(value)
}

function orderedEntries(value: Record<string, unknown>) {
  return Object.entries(value)
    .filter(([key, item]) => !ignoredKeys.has(key) && formatFieldValue(key, item))
    .sort(([left], [right]) => {
      const leftRank = fieldOrderMap.get(left) ?? 999
      const rightRank = fieldOrderMap.get(right) ?? 999
      if (leftRank !== rightRank) return leftRank - rightRank
      return left.localeCompare(right)
    })
}

function plain(value: unknown): string {
  if (value === undefined || value === null) return ""
  if (typeof value === "string" || typeof value === "number") return compact(value)
  if (Array.isArray(value)) return value.map(plain).filter(Boolean).join("\n")
  if (typeof value === "object") {
    return orderedEntries(value as Record<string, unknown>)
      .map(([key, item]) => {
        const text = formatFieldValue(key, item)
        return text ? `${label(key)}：${text}` : ""
      })
      .filter(Boolean)
      .join("\n")
  }
  return compact(value)
}

function label(key: string) {
  return fieldLabels[key] || key
}

/** 审阅端「微调」表单要用的字段标签与排序，与 diff 展示共用一份来源。 */
export function fieldLabelOf(key: string) {
  return label(key)
}

export function fieldRankOf(key: string) {
  return fieldOrderMap.get(key) ?? 999
}

export function isIgnoredField(key: string) {
  return ignoredKeys.has(key)
}

/** 把任意结构化值摊平成与 diff 一致的纯文本（HTML → 文本、数组换行、字段加标签）。 */
export function plainTextOf(value: unknown) {
  return plain(value)
}

/** 文本里是否带富文本标记：带标记的字段在微调框里只展示摊平文本，写入时才落纯文本。 */
export function containsMarkup(value: unknown) {
  return typeof value === "string" && /<[a-z][\s\S]*>/i.test(value)
}

export function localizeFieldText(value: string) {
  let result = value
  Object.entries(fieldLabels)
    .sort((a, b) => b[0].length - a[0].length)
    .forEach(([key, fieldLabel]) => {
      result = result.replace(new RegExp(`(?<![A-Za-z0-9_])${key.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?![A-Za-z0-9_])`, "g"), fieldLabel)
    })
  return result
}

function adviceText(value: unknown): string {
  if (value === undefined || value === null) return ""
  if (Array.isArray(value)) return value.map(adviceText).filter(Boolean).join("；")
  if (typeof value === "object") {
    const item = value as Record<string, unknown>
    const candidate = item.content || item.description || item.text || item.message || item.suggestion
    return candidate === undefined ? "" : adviceText(candidate)
  }
  return compact(value)
}

function stripAdvicePrefix(value: string) {
  return value.replace(/^\s*[-*•]?\s*\d+\s*[.、)]\s*/, "").trim()
}

function isInternalAdvice(value: string) {
  const text = value.toLowerCase()
  return [
    /\b(json|schema|pydantic|resume_data|optimized_resume_data|section_content|section_type|section_title|keywords|tech_stack)\b/i,
    /(字段|属性|key|键名).*(格式|类型|统一|规范|兼容|数组|字符串|对象|结构)/,
    /(数组|字符串|对象|json|结构化).*(格式|形式|字段|解析|兼容)/i,
    /(逗号分隔|分隔的字符串|数组形式|列表形式|数据结构|数据格式)/,
    /(简历解析习惯|ats解析|解析兼容|解析器|可解析)/i,
  ].some((pattern) => pattern.test(text))
}

export function normalizeAiAdviceList(items: unknown[]) {
  const advice = items
    .map((item) => stripAdvicePrefix(adviceText(item)))
    .filter(Boolean)
    .filter((item) => !isInternalAdvice(item))
    .map(localizeFieldText)
    .map(stripAdvicePrefix)
    .filter(Boolean)
  return [...new Set(advice)]
}

function normalizeForCompare(value: string) {
  return value.replace(/\s+/g, " ").replace(/[，。；;,.]/g, "").trim()
}

function compareValue(value: unknown): string {
  if (value === undefined || value === null) return ""
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return normalizeForCompare(String(value))
  if (Array.isArray(value)) {
    const items = value.map(compareValue).filter(Boolean)
    const primitiveLike = value.every((item) => item === undefined || item === null || typeof item !== "object")
    return (primitiveLike ? items.sort() : items).join("|")
  }
  if (typeof value === "object") {
    return Object.entries(value as Record<string, unknown>)
      .filter(([key]) => !ignoredKeys.has(key))
      .map(([key, item]) => [key, compareFieldValue(key, item)] as const)
      .filter(([, item]) => item)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, item]) => `${key}:${item}`)
      .join("|")
  }
  return normalizeForCompare(String(value))
}

function compareFieldValue(key: string, value: unknown) {
  if (key === "keywords" || key === "tech_stack") {
    return splitTagText(value).map(normalizeForCompare).filter(Boolean).sort().join("|")
  }
  return compareValue(value)
}

/** 条目标识：按字段优先级取第一个非空值，保持原来的 `a || b || …` 语义。 */
function itemIdentity(item: unknown): string {
  const record = asRecord(item)
  if (!record) return ""
  return compact(
    record.name || record.company || record.organization || record.school || record.title ||
      record.position || record.role || record.publisher || record.platform,
  )
}

function itemTitle(item: unknown, index: number, fallback: string) {
  return itemIdentity(item) || `${fallback} ${index + 1}`
}

function itemKey(item: unknown, index: number) {
  return itemIdentity(item) || `item_${index}`
}

/**
 * 同名条目按出现次序加后缀：同一所学校读两个学位、两段同名实习都会撞 `itemKey`，
 * 不加后缀 diff 会把两条互相错配（结果是「本科那条被硕士那条覆盖」）。
 */
function keyedEntries(list: unknown[]) {
  const seen = new Map<string, number>()
  return list.map((item, index) => {
    const base = itemKey(item, index)
    const count = (seen.get(base) || 0) + 1
    seen.set(base, count)
    return { item, key: count === 1 ? base : `${base}#${count}` }
  })
}

/** 同一套后缀规则作用到已算好的条目上（toEntries 已经按顺序映射）。 */
function uniqueEntryKeys(entries: ComparableEntry[]) {
  const seen = new Map<string, number>()
  return entries.map((entry) => {
    const count = (seen.get(entry.key) || 0) + 1
    seen.set(entry.key, count)
    return count === 1 ? entry : { ...entry, key: `${entry.key}#${count}` }
  })
}

function sectionTitle(data: unknown, key: string) {
  const title = sectionTitlesOf(data)?.[key]
  const custom = customSectionsOf(data).find((item) => asRecord(item)?.id === key)
  return (
    (typeof title === "string" && title) ||
    builtInTitles[key] ||
    (asRecord(custom)?.title as string) ||
    "自定义模块"
  )
}

/** 模块标题变更的 DiffItem entryKey（翻译/重命名模块标题时可勾选）。 */
export const SECTION_TITLE_ENTRY_KEY = "__title__"

function titleDiffItem(sectionKey: string, currentData: unknown, nextData: unknown): DiffItem | null {
  const before = sectionTitlesOf(currentData)?.[sectionKey]
  const after = sectionTitlesOf(nextData)?.[sectionKey]
  const beforeText = typeof before === "string" ? before.trim() : ""
  const afterText = typeof after === "string" ? after.trim() : ""
  if (!afterText || beforeText === afterText) return null
  if (!beforeText) {
    return { id: `${sectionKey}:${SECTION_TITLE_ENTRY_KEY}:added`, kind: "added", sectionKey, entryKey: SECTION_TITLE_ENTRY_KEY, title: "模块标题", after: afterText }
  }
  return { id: `${sectionKey}:${SECTION_TITLE_ENTRY_KEY}:modified`, kind: "modified", sectionKey, entryKey: SECTION_TITLE_ENTRY_KEY, title: "模块标题", before: beforeText, after: afterText }
}

export function sectionValue(data: unknown, key: string): unknown {
  const record = asRecord(data)
  if (!record) return undefined
  if (record[key] !== undefined) return record[key]
  return customSectionsOf(data).find((item) => asRecord(item)?.id === key)
}

/**
 * 审阅「微调」的起点：从提案里取出这一条的结构化原值。
 * 标题项返回标题字符串；列表里找不到就退回整段值，避免微调框空白。
 */
export function entryPayloadOf(data: unknown, sectionKey: string, entryKey: string): unknown {
  if (entryKey === SECTION_TITLE_ENTRY_KEY) {
    const title = sectionTitlesOf(data)?.[sectionKey]
    return typeof title === "string" ? title : ""
  }
  const value = sectionValue(data, sectionKey)
  if (sectionKey === "summary") {
    const record = asRecord(value)
    return record && "content" in record ? record.content : value
  }
  const record = asRecord(value)
  const list = record && Array.isArray(record.items) ? record.items : asArray(value)
  if (!list) return value
  const matched = keyedEntries(list).find((entry) => entry.key === entryKey)
  return matched ? matched.item : value
}

function hasSection(data: unknown, key: string) {
  const record = asRecord(data)
  if (!record) return false
  if (Object.prototype.hasOwnProperty.call(record, key)) return true
  return customSectionsOf(data).some((item) => asRecord(item)?.id === key)
}

function toEntries(sectionKey: string, value: unknown, fallbackTitle: string): ComparableEntry[] {
  if (sectionKey === "summary") {
    const content = asRecord(value)?.content ?? value
    const text = plain(content)
    return text ? [{ key: "summary", title: fallbackTitle, text, compareText: compareValue(content) }] : []
  }
  const record = asRecord(value)
  if (record && Array.isArray(record.items)) {
    return uniqueEntryKeys(
      record.items.map((item, index) => ({
        key: itemKey(item, index),
        title: itemTitle(item, index, "条目"),
        text: plain(item),
        compareText: compareValue(item),
      })),
    )
  }
  if (Array.isArray(value)) {
    return uniqueEntryKeys(
      value.map((item, index) => ({
        key: itemKey(item, index),
        title: itemTitle(item, index, fallbackTitle),
        text: plain(item),
        compareText: compareValue(item),
      })),
    )
  }
  const text = plain(value)
  return text ? [{ key: sectionKey, title: fallbackTitle, text, compareText: compareValue(value) }] : []
}

function cloneData<T>(value: T): T {
  return JSON.parse(JSON.stringify(value ?? null))
}

function replaceListEntry(current: unknown[], next: unknown[], override: unknown, entryKey: string, kind: DiffKind) {
  const currentIndex = keyedEntries(current).findIndex((entry) => entry.key === entryKey)
  if (kind === "removed" && override === undefined) {
    return currentIndex === -1 ? current : current.filter((_, index) => index !== currentIndex)
  }
  // 人工微调过的那条直接作为载荷，不再从提案里按 key 找——用户可能把标识字段一起改了。
  const payload = override !== undefined ? override : keyedEntries(next).find((entry) => entry.key === entryKey)?.item
  if (payload === undefined) return current
  if (currentIndex === -1) return [...current, cloneData(payload)]
  const result = [...current]
  result[currentIndex] = cloneData(payload)
  return result
}

function setSectionValue(data: DataRecord, sectionKey: string, value: unknown) {
  if (Object.prototype.hasOwnProperty.call(data, sectionKey) || builtInTitles[sectionKey]) {
    data[sectionKey] = value
    return
  }
  const customs = asArray(data.custom_sections)
  const index = (customs || []).findIndex((item) => asRecord(item)?.id === sectionKey)
  if (customs && index >= 0) customs[index] = value
}

/**
 * 勾选 + 微调的合并结果。
 *
 * `overrides` 的键是 DiffItem.id，值是审阅卡里人工改过的结构化条目（标题项是字符串）。
 * 有覆盖值时以覆盖值为准，其余沿用提案数据——这样「先改再应用」不会两套数据打架：
 * 预览、落库走的都是这一个函数。
 */
export function buildSelectedResumeData<T>(
  currentData: T,
  optimizedData: unknown,
  selectedIds: string[],
  sections: SectionDiff[],
  overrides: Record<string, unknown> = {},
): T {
  const selected = new Set(selectedIds)
  const result = cloneData(currentData || {}) as DataRecord
  const next = cloneData(optimizedData || {}) as DataRecord

  sections.forEach((section) => {
    section.changes.forEach((change) => {
      if (!selected.has(change.id)) return
      // 模块标题只走下面的 layout 合并；混进来会把整段 section 一起替换掉。
      if (change.entryKey === SECTION_TITLE_ENTRY_KEY) return
      const override = overrides[change.id]
      const currentValue = sectionValue(result, section.key)
      const nextValue = sectionValue(next, section.key)
      const currentArray = asArray(currentValue)
      const nextArray = asArray(nextValue)
      if (currentArray || nextArray) {
        setSectionValue(result, section.key, replaceListEntry(currentArray || [], nextArray || [], override, change.entryKey, change.kind))
        return
      }
      const currentRecord = asRecord(currentValue)
      const nextRecord = asRecord(nextValue)
      if (currentRecord && nextRecord && Array.isArray(currentRecord.items) && Array.isArray(nextRecord.items)) {
        setSectionValue(result, section.key, {
          ...currentRecord,
          ...nextRecord,
          items: replaceListEntry(currentRecord.items, nextRecord.items, override, change.entryKey, change.kind),
        })
        return
      }
      if (change.kind === "removed") {
        setSectionValue(result, section.key, undefined)
      } else {
        setSectionValue(result, section.key, cloneData(override !== undefined ? override : nextValue))
      }
    })
  })

  const nextLayout = layoutOf(next)
  if (nextLayout) {
    // 模块标题按勾选逐项合并（翻译/重命名标题可单独取消），其余 layout 字段整组合并
    const titleChanges = sections
      .flatMap((section) => section.changes)
      .filter((change) => change.entryKey === SECTION_TITLE_ENTRY_KEY && selected.has(change.id))
    const mergedTitles: DataRecord = { ...(asRecord(layoutOf(result)?.section_titles) || {}) }
    const nextTitles = asRecord(nextLayout.section_titles) || {}
    for (const change of titleChanges) {
      const override = overrides[change.id]
      const title = typeof override === "string" && override.trim() ? override.trim() : nextTitles[change.sectionKey]
      if (title !== undefined) mergedTitles[change.sectionKey] = title
    }
    result.layout = {
      ...(layoutOf(result) || {}),
      ...nextLayout,
      section_titles: mergedTitles,
    }
  }
  return result as T
}

export function diffSection(sectionKey: string, currentValue: unknown, nextValue: unknown, title = builtInTitles[sectionKey] || "当前模块"): DiffItem[] {
  const currentEntries = toEntries(sectionKey, currentValue, title)
  const nextEntries = toEntries(sectionKey, nextValue, title)
  const currentMap = new Map(currentEntries.map((item) => [item.key, item]))
  const nextMap = new Map(nextEntries.map((item) => [item.key, item]))
  const changes: DiffItem[] = []

  nextEntries.forEach((item) => {
    const before = currentMap.get(item.key)
    if (!before) {
      changes.push({ id: `${sectionKey}:${item.key}:added`, kind: "added", sectionKey, entryKey: item.key, title: item.title, after: item.text })
      return
    }
    if (before.compareText !== item.compareText) {
      changes.push({ id: `${sectionKey}:${item.key}:modified`, kind: "modified", sectionKey, entryKey: item.key, title: item.title, before: before.text, after: item.text })
    }
  })

  currentEntries.forEach((item) => {
    if (!nextMap.has(item.key)) changes.push({ id: `${sectionKey}:${item.key}:removed`, kind: "removed", sectionKey, entryKey: item.key, title: item.title, before: item.text })
  })

  return changes
}

export interface UnchangedEntry {
  key: string
  title: string
  text: string
}

/** 审阅模式用：变更（含模块标题）+ 未变更条目（提案前后一致，含提案后用户手动改过导致的零差异）。 */
export function sectionEntriesForReview(sectionKey: string, currentData: unknown, nextData: unknown, title?: string): { changes: DiffItem[]; unchanged: UnchangedEntry[] } {
  const resolvedTitle = title || sectionTitle(currentData, sectionKey) || sectionTitle(nextData, sectionKey)
  const currentValue = sectionValue(currentData, sectionKey)
  const nextValue = sectionValue(nextData, sectionKey)
  const currentEntries = toEntries(sectionKey, currentValue, resolvedTitle)
  const nextEntries = toEntries(sectionKey, nextValue, resolvedTitle)
  const currentMap = new Map(currentEntries.map((item) => [item.key, item]))
  const nextMap = new Map(nextEntries.map((item) => [item.key, item]))
  const changes = [titleDiffItem(sectionKey, currentData, nextData), ...diffSection(sectionKey, currentValue, nextValue, resolvedTitle)].filter(
    (item): item is DiffItem => item !== null,
  )
  const changedKeys = new Set(changes.map((item) => item.entryKey))
  const unchanged: UnchangedEntry[] = []
  nextEntries.forEach((item) => {
    const before = currentMap.get(item.key)
    if (!before || changedKeys.has(item.key)) return
    unchanged.push({ key: item.key, title: item.title, text: item.text })
  })
  currentEntries.forEach((item) => {
    if (nextMap.has(item.key) || changedKeys.has(item.key)) return
    unchanged.push({ key: item.key, title: item.title, text: item.text })
  })
  return { changes, unchanged }
}

export function diffResume(currentData: unknown, optimizedData: unknown): SectionDiff[] {
  if (!currentData || !optimizedData) return []
  const order = [
    ...new Set([
      "basics",
      ...sectionOrderOf(currentData),
      ...sectionOrderOf(optimizedData),
      "summary",
      "education",
      "skills",
      "work",
      "projects",
      "awards",
      ...customSectionIdsOf(currentData),
      ...customSectionIdsOf(optimizedData),
    ]),
  ].filter(Boolean)

  return order
    .filter((key) => hasSection(currentData, key) || hasSection(optimizedData, key))
    .map((key) => {
      const title = sectionTitle(optimizedData, key) || sectionTitle(currentData, key)
      return {
        key,
        title,
        changes: [titleDiffItem(key, currentData, optimizedData), ...diffSection(key, sectionValue(currentData, key), sectionValue(optimizedData, key), title)].filter(
          (item): item is DiffItem => item !== null,
        ),
      }
    })
    .filter((section) => section.changes.length)
}
