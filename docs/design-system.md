# VitaFlow 设计系统

> 定位：规范化文档。描述项目当前 UI 的事实基线，并对不一致处给出**目标态规则**；发现的存量偏差统一收敛到文末「存量改造项」，不在本文档交付时修改代码。
>
> 受众：AI 结对协作（规则速查置顶）+ 前端开发者参考。

---

## 0. AI 协作速查

改前端时逐条遵守；与正文冲突以正文为准。

1. **只用 Tailwind 原子类**，不写自定义 CSS/SCSS（全局例外仅 `src/styles/index.css`）。
2. **灰阶只用 `zinc-*`**，禁止 `gray-*`/`slate-*`（存量 gray 见文末清单，遇到即改）。
3. **主操作色是黑（`zinc-900`）**，不是蓝色；`blue-600` 只用于链接、强调、信息提示、选中焦点。
4. 新 UI 组件先找 `src/components/ui/`，没有再用 shadcn-vue 生成后改造，禁止直接在业务组件里手写完整弹窗/下拉等复杂控件。
5. 弹窗一律遵循 ConfirmDialog 结构模式（Teleport + 遮罩 `bg-zinc-950/40 backdrop-blur-sm` + `rounded-2xl shadow-xl` + 底部灰底按钮区）。
6. 图标只用 `lucide-vue-next`，默认尺寸 `h-4 w-4`。
7. 轻提示用 `showGlobalToast()`（`src/utils/toast.ts`），不要自造 toast。
8. 不引入暗色模式、不使用 `dark:` 前缀——项目不支持。
9. 语义色：成功 `emerald-600`、危险 `red-600`、警告 `amber-500`，不要换其他色相。
10. 全局样式/浏览器兜底只能加在 `src/styles/index.css`，且需注释说明原因。
11. **扁平优先**：静态内容不叠「边框 + 阴影 + 底色」三件套；选中/聚焦只给一处信号（浅灰底 `bg-zinc-100` 或描边），阴影只留给浮层（下拉、弹窗、抽屉）。
12. **不用胶囊**：`rounded-full` 仅限头像、圆形图标按钮、圆点；分段控件、筛选标签、状态标记一律 `rounded-md`，选中态用 `bg-zinc-100`/`bg-zinc-200/70`，不用「黑底胶囊 + 内嵌白块 + 阴影」。圆角大小按 §1 圆角阶梯递进（行 6 / 控件 8 / 容器 12 / 大容器与弹窗 16），**不要把所有圆角统一压到 `rounded-md`**。
13. 图标默认单色（`text-zinc-400`，选中 `text-zinc-500`），不做彩色图标底块与 hover 上浮动画。
14. **预览类卡片只留一圈框**：缩略图（简历预览、模板预览）一律贴边由卡片自身的 `ring`/`border` 当唯一边框，预览组件用 `bare` 模式去掉自己的 `border`+`rounded`，底部用渐变过渡到白，不要再套「外层边框 + 内层描边 + 页面白底」三层，否则会出现明显的框中框。

---

## 1. 技术栈基线

| 项 | 事实 |
|---|---|
| 框架 | Vue 3 + Vite + TypeScript |
| 样式 | Tailwind CSS **v4**（`@import "tailwindcss"` 于 `src/styles/index.css`），无 SCSS |
| 无头组件 | radix-vue（shadcn-vue 模式，`components.json`：`cssVariables: false`） |
| 图标 | lucide-vue-next（约 45+ 文件使用），个别自绘 SVG（BrandLogo、FlowAgentIcon、FlowPointIcon） |
| 暗色模式 | **不支持**。全库无 `dark:`、`.dark`、`prefers-color-scheme` |
| 全局样式 | 唯一入口 `src/styles/index.css`：body 背景 `#f7f8fb`、文字 `#111827`、字体栈 Inter → system-ui → "Microsoft YaHei"；含 `.thin-scrollbar`、富文本重置、旧浏览器 oklch/color-mix 兜底变量 |
| 注意 | `tailwind.config.js` 在 v4 下**不被读取**（CSS 未加 `@config`），其中 `primary: #2563eb` 等定义全部无效，属死配置 |

**字号/字体**：标题 `font-semibold tracking-tight`，正文 `text-sm`（14px），辅助 `text-xs`。代码中大量 `text-[14px]` 任意值与 `text-sm` 等价，新代码一律用阶梯（`text-xs/sm/base`）。

**圆角阶梯**（四档，按元素尺寸递进；勿随意新造）

| 类 | 值 | 用途 |
|---|---|---|
| `rounded-full` | 9999px | 仅头像、圆形图标按钮、状态圆点；**不作为分段控件/标签/按钮的形状** |
| `rounded-md` | 6px | 密集列表行（侧栏简历行、会话行、「新对话」）、菜单项、内联小徽标、图标按钮的 hover 底、分段控件容器 |
| `rounded-lg` | 8px | 按钮（`Button.vue` 基础类即此档）、单行输入框、搜索框、附件缩略图、模块导航项、模式切换 tab、移动端底部 tab |
| `rounded-xl` | 12px | 卡片（简历卡、模板卡）、消息气泡、所有浮层（下拉面板、右键菜单、确认气泡、斜杠指令菜单）、大虚线空态框 |
| `rounded-2xl` | 16px | 大容器与弹窗：对话输入区（composer）、`AppDialog`、`ConfirmDialog`、导出进度、轻量提示卡 |

判断口诀：**行 6 / 控件 8 / 容器 12 / 大容器与弹窗 16**。看过 WorkBuddy 的实测基线——删除确认气泡圆角 12px、气泡内按钮 8px、侧栏列表行接近直角（4–6px）、对话输入框 16px（大高度容器），与上表一致；单调地把所有圆角压到 6px 会让 40px 以上的容器明显发「方」，把列表行提到 12px 又会回到胶囊感。

**对话输入区（composer）的结构基线**（对齐 WorkBuddy）：外层 `rounded-2xl border border-zinc-200` + 极轻阴影，聚焦时只把边框提到 `zinc-300` 并加 `ring-2 ring-zinc-900/[0.04]`；底部工具条左起为「`+` 加附件 → 图标+模型名+⌄ 的纯文字 Select」，右侧为「新对话 / 清空 / 圆形发送（`rounded-full bg-zinc-900`，禁用变 `bg-zinc-200`）」；输入区与工具条之间不留分隔线。

**Button 的圆角覆盖坑**：`Button.vue` 基础类已含 `rounded-lg`，调用处**不要再写 `rounded-md`**（Tailwind 同组工具类按尺度排序，基础类会盖掉它，代码与实际渲染不一致）。需要更圆时写 `rounded-xl`（大于基础档才生效），需要更方时只能改基础类。

**阴影阶梯**：默认**无阴影**（静态卡片用 `ring-1 ring-zinc-200/70` 或 `border-zinc-200` 表达边界）；浮层才给 `shadow-lg` / `shadow-xl`（下拉、弹窗、抽屉、toast）。聚焦态用 `ring-1 ring-zinc-300`、`focus-within:ring-2 ring-zinc-900/[0.04]` 表达，不用彩色 glow。编辑器画布内允许自绘 rgba 阴影，业务页面不允许。

**z-index 约定**（全站递增，新层级插在相邻档之间）：

| 档 | 值 | 用途 |
|---|---|---|
| 页头/编辑器顶栏 | `z-20` / `z-40` | sticky 导航 |
| 弹窗遮罩 | `z-[100]` | ConfirmDialog 及所有 modal |
| 下拉/浮层 | `z-[200]` | Select/Dropdown/Popover content、侧栏右键菜单 |
| 锚定确认气泡 | `z-[210]` | `ConfirmPopover`，要盖住弹窗与下拉 |
| 全局 toast | `z-[9999]` | toast.ts |
| tooltip | `z-[99999]` | 跨组件 tooltip |

---

## 2. 设计 Token（语义 → Tailwind 类映射）

> 本项目不定义自定义 token 变量，语义靠「映射表 + 使用规则」约束；映射对象是 Tailwind 默认色阶。

### 2.1 色彩

| 语义 | Tailwind 类 | 色值 | 使用场景 |
|---|---|---|---|
| 主操作色（黑） | `bg-zinc-900` / hover `bg-zinc-800` | #18181b | 主按钮、管理后台选中态、toast 底、深色徽标 |
| 强调色（蓝） | `text-blue-600` / `bg-blue-50` 等 | #2563eb | 链接、进度/信息图标、编辑器标题聚焦态、蓝底浅色提示区 |
| 成功 | `text-emerald-600` / 底 `emerald-50`、toast 图标 `emerald-400` | #059669 | 保存成功、成功 toast |
| 危险 | `bg-red-600` / hover `bg-red-700`、文字 `text-red-600`、底 `red-50` | #dc2626 | danger 按钮、删除确认、错误文案 |
| 警告 | `text-amber-500` | #f59e0b | 保存中状态、琥珀色提示（「未选择」等） |

**规则**：
- 主按钮 = `bg-zinc-900 text-white hover:bg-zinc-800`；蓝色不是主操作色，禁止把主按钮改成蓝底。
- 语义色只允许上表色值；需要浅底时用对应 `-50` 色。
- 唯一扩展：能力/标签类徽标允许 `purple`（`bg-purple-50 border-purple-200/60 text-purple-700`，如「多模态」标签）；purple 不得用于按钮、文字正文或状态反馈。
- 灰阶一律 `zinc`。`gray-*` 是存量偏差，新代码禁止。

### 2.2 背景分层

| 层 | 类 | 说明 |
|---|---|---|
| 页面底 | body `#f7f8fb`；局部 `bg-zinc-50/30~50` | 浅灰底 |
| 卡片 | `bg-white border border-zinc-200 shadow-sm` | 内容承载单元 |
| 嵌入区 | `bg-zinc-50/50` ~ `bg-zinc-100/80` | 卡片内的次级分区 |
| 毛玻璃页头 | `bg-white/80 backdrop-blur-md` | 用户端/编辑器顶栏 |
| 遮罩 | `bg-zinc-950/40 backdrop-blur-sm` | 弹窗背景 |

### 2.3 文字分级

| 层级 | 类 | 用途 |
|---|---|---|
| 标题 | `text-zinc-900`（ ConfirmDialog 标题用 zinc-900）+ `font-semibold` | 卡片/弹窗标题 |
| 正文 | `text-zinc-700` ~ `text-zinc-800` | 表单值、列表内容 |
| 次要 | `text-zinc-600` | 说明、导航未选中 |
| 辅助/占位 | `text-zinc-500` / `text-zinc-400` | 描述文字、placeholder、禁用图标 |

### 2.4 边框

- 统一 `border-zinc-200`，页面顶栏等轻分隔用透明度变体 `border-zinc-200/60 ~ /80`。
- 卡片内分隔线 `border-zinc-100`。
- 聚焦：输入框 `focus:border-zinc-500`；Select 打开态 `border-zinc-900 ring-1 ring-zinc-900`；编辑器标题输入 `focus:border-blue-500 focus:ring-blue-500`（蓝=聚焦提示，仅此处用）。

---

## 3. 组件规范

基础组件位于 `src/components/ui/`（shadcn-vue 生成后手写改造），通用展示组件在 `src/components/common/`。

### 3.1 Button — `ui/button/Button.vue`

```vue
<Button variant="default|outline|ghost|danger" size="sm|md|icon" :disabled />
```

| variant | 样式 | 场景 |
|---|---|---|
| `default` | 黑底白字（目标 `bg-zinc-900 hover:bg-zinc-800`，存量 `gray-900` 待改） | 主操作，一屏至多一个 |
| `outline` | 白底 `border-gray-200`（待改 zinc-200）`text-gray-900` | 次操作 |
| `ghost` | 透明底 hover `bg-gray-100`（待改 zinc-100） | 图标按钮、工具栏 |
| `danger` | `bg-red-600 text-white hover:bg-red-700` | 删除等破坏性操作 |

- 尺寸：`sm` = `h-8 px-3`；`md` = `h-10 px-4`；`icon` = `h-9 w-9`。统一 `rounded-lg text-sm font-medium`，`disabled:opacity-50`。
- 规则：同一操作区（弹窗底部、卡片尾部）按钮高度必须一致；危险操作旁必须配取消按钮。

### 3.2 弹窗 — 统一外壳 `ui/dialog/AppDialog.vue`

所有弹窗必须基于 `AppDialog`（Teleport + 遮罩 `bg-zinc-950/40 backdrop-blur-sm` + `rounded-2xl shadow-xl max-h-[90vh]` + 头部/底部结构），通过 props 定制：`open`（v-model:open）、`title`/`description`（默认头部）、`maxWidthClass`、`bodyClass`/`headerClass`/`footerClass`、`showClose`/`overlayClose`；`#icon` 插槽加头部图标，`#header`/`#footer` 插槽全自定义。确认类弹窗用 `ConfirmDialog`（已构建于 AppDialog 之上）。默认头部 = title/description + 右上 X；默认底部容器 = `border-t border-zinc-100 bg-zinc-50 px-5 py-4`。

```
<Teleport to="body">
  <Transition>                                   ← 200ms ease-out 进场，scale 95→100
    <div 遮罩: fixed inset-0 z-[100] flex items-center justify-center p-4
              bg-zinc-950/40 backdrop-blur-sm @click.self=关>
      <div 面板: bg-white rounded-2xl shadow-xl w-full max-w-sm overflow-hidden>
        头部: px-5 py-4 flex gap-3
              左: w-10 h-10 rounded-full 图标圆底（破坏性 red-50/red-600，否则 blue-50/blue-600）
              中: 标题 text-zinc-900 font-semibold text-[16px] + 描述 text-sm text-zinc-500
              右: X 关闭钮 text-zinc-400 hover:bg-zinc-100
        底部: px-5 py-4 bg-zinc-50/50 border-t border-zinc-100 flex gap-3
              取消: flex-1 h-10 rounded-xl border-zinc-200 bg-white text-zinc-700
              确认: flex-1 h-10 rounded-xl text-white（破坏性 red-600，否则 zinc-900）
```

- Props 约定：`open`（v-model:open）、`title`、`description`、`confirmText`、`destructive`。
- 规则：确认按钮触发异步时由调用方自行置 loading/禁用；遮罩点击 = 取消。

### 3.3 表单控件

| 控件 | 文件 | 关键样式 |
|---|---|---|
| Input | `ui/input/Input.vue` | `h-10 w-full rounded-xl border-zinc-200 px-3.5 text-[14px]`，hover `border-zinc-300`，focus `border-zinc-500`，`placeholder:text-zinc-400` |
| Textarea | `ui/textarea/Textarea.vue` | 同 Input 圆角/边框风格 |
| Select | `ui/select/Select.vue` | radix-vue SelectRoot；默认 `h-10 border-zinc-200 shadow-sm`，**打开态** `border-zinc-900 ring-1 ring-zinc-900`；`ghost` variant `h-8` 透明底用于工具栏内嵌下拉；面板 `rounded-xl border-zinc-200 shadow-lg z-[200]`，选中项 `focus:bg-zinc-100` |
| Label | `ui/label/Label.vue` | 表单标签 |
| Switch | `ui/switch/Switch.vue` | 布尔开关 |
| DateTimePicker | `ui/DateTimePicker.vue` | 日期时间选择 |

**规则**：表单控件高度统一 `h-10`（ghost 档 `h-8` 仅用于工具栏内嵌）；表单标签一律配 `Label`；错误文案 `text-sm text-red-600 mt-1.5`；表单纵向间距 `space-y-4` 起步。

### 3.4 展示组件

| 组件 | 关键样式 | 备注 |
|---|---|---|
| Card | `ui/card/Card.vue`：`rounded-xl border border-gray-200 bg-white shadow-sm`（gray 待改 zinc） | 卡片外框唯一基准 |
| Badge | `ui/badge/Badge.vue`：`rounded-full border bg-gray-50 px-2.5 py-1 text-xs font-medium text-gray-700`（gray 待改 zinc） | 状态徽标；彩色徽标按语义色 `-50` 底 + `-600/700` 字 |
| Alert | `ui/alert/Alert.vue` | 提示条 |
| Avatar | `ui/avatar/Avatar.vue` | 头像 |
| Tabs | `ui/tabs/`；工作区里的模式切换（内容编辑 / Flow Agent）、模板分类改为扁平 tab：`h-7 rounded-lg px-2.5`，选中 `bg-zinc-100 text-zinc-900`，未选中 `text-zinc-500 hover:bg-zinc-50`，容器无底色只留发丝线 | |
| Pagination | `ui/pagination/PaginationFooter.vue` | 列表分页统一用它 |
| EmptyState | `common/EmptyState.vue` | 空状态统一入口 |
| LoadingButton | `common/LoadingButton.vue` | 带加载态的按钮 |
| BrandLogo | `common/BrandLogo.vue` | 品牌标识 |

### 3.5 Toast（轻提示）

非组件，统一调用 `showGlobalToast(message, "success" | "error")`（`src/utils/toast.ts`）：底部居中 `bottom-10`，黑底 `bg-zinc-900 rounded-xl shadow-xl`，图标 `emerald-400`（成功）/`red-400`（失败），3s 自动消失，全局单例。**禁止**在业务组件里再造 toast/悬浮提示。

### 3.6 新组件准入规则

按优先级：

1. **复用**：先找 `src/components/ui/` 与 `src/components/common/`，props 不够用优先扩展现有组件。
2. **生成改造**：`ui/` 没有对应物时，用 shadcn-vue CLI（`components.json` 已配置）生成到 `ui/` 下，再去掉用不到的 API、把样式统一到本文档规范（替换其默认 slate/gray 为 zinc）。
3. **手写**：仅当控件足够简单（一个 div 级别的静态布局）才允许在业务组件内手写；**弹窗、下拉、日期选择、拖拽类复杂控件一律禁止业务内手写**。
4. 命名：`ui/` 下 PascalCase 单文件或多文件目录（如 `button/Button.vue`）；业务组件按域归目录（`editor/`、`resume/`、`ai/`、`admin/`、`home/`、`preview/`、`announcement/`、`layout/`）。

---

## 4. 布局模式

断点：`sm` 640 / `md` 768 / `lg` 1024。移动优先，降级规则各模式内注明。

### 4.1 用户端框架 — `layout/MainLayout.vue`

- sticky 顶栏：`sticky top-0 z-20 border-b border-zinc-200/60 bg-white/80 backdrop-blur-md`，内容行 `h-16 max-w-7xl mx-auto px-4 sm:px-6`。
- 页面内容统一 `max-w-7xl mx-auto px-4 sm:px-6` 容器宽度。
- 移动端：汉堡菜单展开全宽下拉层（`sm:hidden`，`bg-white/95 backdrop-blur-xl`）；未登录直接显示登录/注册按钮。

### 4.2 简历编辑器 — `views/ResumeEditor.vue`

- 独立于 MainLayout：自有顶栏 `h-14 md:h-16`（返回、可内联编辑的标题、保存状态指示、编辑/AI 分段切换 pill、预览/导出）。
- 保存状态指示：`text-amber-500`（保存中）/`text-red-500`（失败）/`text-zinc-500` + `emerald-500` 图标（已保存）。
- 编辑模式：`ModuleSidebar`（模块导航，移动端横置顶部）+ `ResumeFormPanel`（表单区）+ 右侧 A4 预览，之间有拖拽 resizer（`cursor-col-resize w-4`）。
- AI 模式：`ResumeAiChatPanel` 白底对话面板接管主区。
- 编辑器内允许大量精细化样式（自绘阴影、`absolute` 微调），业务页面不模仿。

### 4.3 管理后台 — `admin/AdminShell.vue`

- 容器 `max-w-7xl mx-auto flex gap-6 px-4 py-6 sm:px-6 lg:py-10`。
- 桌面：左侧 `hidden lg:block w-56 shrink-0` 白卡侧边栏（`rounded-2xl border-zinc-200 bg-white p-3 shadow-sm`），选中项 `bg-zinc-900 text-white`，未选中 `text-zinc-600 hover:bg-zinc-100`。
- 移动端（`lg:hidden`）：侧边栏降级为顶部横向滚动 tab 条。
- 内容区：每个功能板块一张 `rounded-2xl border-zinc-200 bg-white shadow-sm` 卡片；卡片内部用 tab 或分区标题组织（如 AiSettingsManager 的「模型目录 / 模型策略」内嵌 tab）。
- 数据表格/列表行 hover `bg-zinc-50`，操作按钮用 Button ghost/outline sm。

### 4.4 新页面开发 checklist

- [ ] 外层容器是否 `max-w-7xl mx-auto px-4 sm:px-6`？
- [ ] 是否复用 MainLayout（或确有理由独立如编辑器）？
- [ ] 页面底色遵循 body 默认，未另设背景？
- [ ] 所有灰阶是 zinc？语义色符合 2.1 表？
- [ ] 弹窗走 ConfirmDialog 模式 / 复杂控件来自 ui/？
- [ ] toast 用 showGlobalToast？
- [ ] 移动端（<640px）检查过：导航可用、卡片不溢出、弹窗不超屏？
- [ ] 图标 lucide、默认 `h-4 w-4`？
- [ ] z-index 落在约定档位？

---

## 5. 存量改造项

> 文档即目标态；以下偏差按表逐项勾销，改造时同步更新本表。

| # | 位置 | 现状 | 目标 | 改造方式 | 风险 | 状态 |
|---|---|---|---|---|---|---|
| 1 | `tailwind.config.js` + `components.json` | v4 不读取该配置，`primary: #2563eb` 等为死配置；`components.json` tailwind 段仍引用它 | 删除 `tailwind.config.js`，`components.json` 的 tailwind 段清空/移除；`baseColor: "slate"` 改 `"zinc"` | 直接删除（无运行时引用） | 低 | 已完成 |
| 2 | 全库 30 处 `gray-*`（17 文件） | 与 zinc 混用，含核心组件 Button/Card/Badge/Label/Separator/Switch/Avatar/Alert | 全部替换为对应 `zinc-*` | 机械替换 + `npx vue-tsc --noEmit` + 逐页目检 | 低（数量少） | 已完成 |
| 3 | 手写弹窗外壳：ResumeList 内联 AI 生成壳（AiGenerateDialog 仅表单体）、`AnnouncementModal`、`PointsAdjustModal`、`CreateResumeModal`、`ResumeShareDialog`、`AvatarCropperModal` | 各自手写 Teleport/遮罩/按钮区，z 层（50/80/90/100/120）、圆角、阴影漂移 | 统一迁移到 `AppDialog`（3.2）；`ConfirmDialog` 重建其上 | 已抽取 AppDialog 并逐个迁移，保留业务内容 | 中（需逐个回归） | 已完成 |
| 4 | `ui/dialog/Dialog.vue` | 空壳（仅 slot，无实现） | 删除（radix-vue Dialog 需要时再按 3.6 生成） | 直接删除 | 低 | 已完成 |
| 5 | 表单控件高度 | Input `h-10` vs Select `h-9`，同排不齐 | 统一为 `h-10`（Select 默认档提高，ghost 档不变） | 改 Select 触发器高度 + 目检所有同排场景 | 中（视觉回归） | 已完成 |
| 6 | 任意字号值 | 大量 `text-[14px]`（与 `text-sm` 等价）、`text-[16px]` 等 | 新代码用阶梯；存量遇改即换，不专项清理 | 渐进 | 低 | 渐进中 |
| 7 | body 背景 `#f7f8fb` | 非 Tailwind 色阶值（zinc-50 是 `#fafafa`） | 保留（首页观感依赖此值），在 index.css 注释说明与 zinc-50 的关系 | 加注释 | 低 | 已完成 |
| 8 | 工作区外壳（侧栏、对话页、编辑器顶栏、模板中心、简历卡） | 圆角 1.25~2rem、选中态「白底 + 描边 + 阴影」、图标彩色底块、hover 位移与光斑 | 统一扁平：选中态只留浅灰底、图标单色、静态无阴影；圆角按阶梯分层（列表行 6 / 控件 8 / 卡片与浮层 12 / 弹窗 16），不整体压到 6px | 逐组件改类名 + Playwright 截图目检 | 中（纯样式） | 已完成 |
| 9 | `ResumeAiChatPanel` 的 Markdown 列表 | Tailwind v4 preflight 清掉 `ul/ol` 符号与缩进，AI 回复的要点变成无符号的连续段落 | 在 `index.css` 补 `.chat-markdown` 的 `!important` 列表重置（与富文本区同一套做法） | 全局样式补一段 | 低 | 已完成 |

> 附带修复（审计 ticket 1~11 新增/重写组件时发现）：`ResumeAiChatPanel` 与 `ResumeList` 各自内造 toast → 统一 `showGlobalToast`；AI 图片灯箱 `z-[9999]` → 弹窗档 `z-[100]`；`PointsAdjustModal` 主按钮 blue-600 → zinc-900（黑主蓝辅）、输入聚焦蓝环 → zinc。
