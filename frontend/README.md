# ResumeFlow Frontend

Vue3 + TypeScript + Vite 前端。界面以工作区外壳（`/workspace/*`）为主：左侧是简历与会话入口，中间是工作区首页 / 模板中心 / 编辑器，右侧是 A4 预览。公开访问的只有首页、登录页、模板页和分享页。

## 启动

```bash
npm install
cp .env.example .env
npm run dev
```

默认 `http://localhost:5173`，`VITE_API_BASE_URL` 指向后端 API（示例为 `http://127.0.0.1:8000/api`）。

## 常用命令

```bash
npm run dev       # 开发服务器
npm run build     # vue-tsc 类型检查 + 生产构建
npm run preview   # 预览构建产物
npm test          # vitest 单元测试
```

## 说明

- Axios 在 `src/api/request.ts` 统一封装，自动携带 token 并解包 `{ code, message, data }`；AI 流式接口走 `src/api/ai.ts` 的 `fetch` + `ReadableStream`。
- Pinia 管理 4 个 store：`user`（登录态、积分）、`resume`（列表）、`editor`（当前简历、脏标记、保存、预览刷新）、`chat`（会话列表与当前会话）。
- 编辑器分「内容编辑 / Agent」两种模式：内容编辑走结构化表单，Agent 走对话。修改、润色、翻译、JD 优化、生成等 AI 能力都收敛在这一条对话里，不再有独立的功能按钮。
- AI 产出的是「待确认变更」，用户点「应用」后才写入简历，前端不直接改数据。
- 编辑器使用 VueUse 防抖自动保存；右侧 A4 预览通过后端 `preview-html` 接口获取 HTML，PDF 导出前会先保存当前简历。
- 路由守卫在 `src/router/index.ts`：未登录跳 `/login`，管理员页额外校验 `requiresAdmin`（后端仍会再校验一次，前端隐藏不是安全边界）。
- 样式只用 Tailwind 原子类，色彩、圆角、弹窗结构等规则见 [`../docs/design-system.md`](../docs/design-system.md)。
- 组件模板有**多个根节点**、或根只有 `<Teleport>`（各类弹窗）时，外部传的 `class` / `style` / `id` 会被 Vue **静默丢弃**——不报错、界面也没变化，极易被误判成样式优先级问题。这类组件必须 `defineOptions({ inheritAttrs: false })` 并在语义根（面板层，不是遮罩）上 `v-bind="$attrs"`；有 `v-if` / `v-else` 多分支的要逐分支绑定。新增组件前先确认自己属于哪一类。

## 目录约定

- `views/`：路由级页面
- `components/`：按领域拆分——`editor/`（表单与模块侧栏）、`preview/`（A4 / HTML / PDF 预览）、`ai/`（对话面板、思考过程、变更确认）、`resume/`（列表、创建、导入、分享）、`templates/`（模板缩略预览）、`workspace/`（工作区各面板）、`admin/`、`ui/`（基础控件）
- `composables/`：有状态逻辑与浏览器能力封装，统一 `useXxx` 命名
- `stores/`：跨组件共享状态
- `api/`：每个业务域一个文件，`request.ts` 是统一封装；后端地址只从 `request.ts` 导出的 `API_BASE_URL` 取（流式 fetch 也一样）
- `utils/`：无副作用的纯函数（`errorText.ts` 统一错误文案口径，中文提示透传、英文传输层报错换兜底文案）
- `lib/`：带第三方依赖的能力封装（`markdown.ts` 渲染、`sanitize.ts` 富文本白名单）
