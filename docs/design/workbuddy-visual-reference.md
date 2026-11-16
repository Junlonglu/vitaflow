# WorkBuddy 界面复刻参考（v2）

对 WorkBuddy 桌面端（`@genie/workbuddy-desktop@5.9.3`，Electron 37.10.3-43，2026-10-27 构建，commit `e3708861`）的拆解，用于在 ResumeFlow 中复刻它的视觉语言与交互习惯。**本文只描述观察到的实现，不代表设计意图**；实施时结合 [`../design-system.md`](../design-system.md) 的既有约定取舍。

> v2 相对 v1 的变化：接入 REA 静态应用图，新增**窗口层**（主进程 BrowserWindow 配置）、**JS 里的布局常量**（侧栏/详情面板的真实夹紧区间）、**骨架屏规格**、**依赖与技术栈**；修正标题栏高度的三级来源；上一版"侧栏宽度未知"的结论已被证伪。

## 0. 证据与方法

| 项 | 内容 |
| --- | --- |
| 目标 | `D:\Softwares\WorkBuddy\resources\app.asar`（182 MB，9893 个条目）、`main/index.js`、`main/main-bootstrap.js` |
| 静态拆包 | 自写 ASAR 头解析 + 选择性提取，只读取渲染层 CSS/JS 与主进程 bundle，未执行目标程序 |
| REA 证据 | `inspect_desktop_application`（→ Electron，provider `rea-artifact-graph`，authority `shipped-artifact`）；`analyze_javascript_application` 分析 5 个壳层 chunk（→ 13.6 万 AST 节点、8382 条发现，语义图 6 万节点上限，状态 `partial`）；`javascript-routes` 视图（→ 仅 1 条路由候选） |
| REA 证据 ID | `ev_98ee34cc…a8f54ea67`（应用图）、`ev_2441f71…2adc71a5`（路由视图） |
| 未做到 | **运行时未验证**。目标包不暴露 CDP：5 个监听端口（18488/49737/49738/63368/51784）均无 `/json/version`，其中 51784 是带鉴权的自有 API。因此所有结论为"静态"，交互手感（spring 曲线、拖拽阻尼）无实测 |
| 主要证据文件 | `renderer/index.html`（内联首屏脚本）、`tokens-*.css`（3054 行 token）、`src-*.css`（`--cr-*` 聊天主题）、`contexts-*.css`（页面骨架）、`contexts-Cfdc2AZw.js`（布局常量）、`main-bootstrap.js`（窗口配置） |

## 1. 四层结构：这是复刻的关键

```
窗口层     BrowserWindow: 无边框 + 原生覆盖按钮 + 窗口底色（#fff / #1e1e1e）+ 可选毛玻璃
  └ 基色桥  --vscode-editor-background / --vscode-font-family（沿用 VS Code 主题变量）
      └ palette     原始色阶 --wb-palette-*
          └ semantic    语义 --wb-color-* / --wb-bg-* / --wb-text-* / --wb-border-*
              └ component   组件 --wb-button-* / --wb-input-* / --wb-card-* …
并行旁路：--cr-*（聊天面独立主题层，light/dark 各 138 个变量）
```

- **基色来自 VS Code 主题变量桥**：`body{background:var(--vscode-editor-background,inherit);color:var(--vscode-editor-foreground,#3b3b3b);font-family:var(--vscode-font-family,…);font-size:var(--vscode-font-size,13px)}`。`index.html` 里还专门为 `body[data-vscode-theme-name="IDE Light"]` 补了缺失的字体变量。**这意味着 WorkBuddy 的"底色 + 基础字"不是自己定义的，而是继承 IDE 主题**，`--wb-*` 是叠在上面的一层。
- **默认主题是 IDE Light，且皮肤与系统明暗解耦**。`index.html` 注释明确写了：背景兜底刻意**不跟随** `prefers-color-scheme`，否则「系统深色 + 用户用浅色皮肤」会在骨架屏拆除到皮肤 CSS 生效之间闪一帧 `#1e1e1e`。真正的明暗由 `ThemeManager` / `skin-manager` 在 module 执行期写 class 决定。
- **组件库是 TDesign**（CSS 里 `t-button` 1043 次、`t-table` 427、`t-dialog` 311，无 antd/arco），业务层用 BEM 覆盖：`conversation-*` / `wb-home-*` / `cr-im-message*`。
- **聊天面有独立 `--cr-*` 主题层**，输入框、消息列表、工具卡都在这一层，不跟随 `--wb-*` 改动。
- 命名规律：`--wb-<组件>-<属性>-<状态>`，状态后缀 `-hover/-active/-disabled/-focus`。

复刻含义：**不要抄单色值，抄层结构**。换品牌只改 palette 与 semantic 两层，组件层不动。ResumeFlow 没有 VS Code 主题系统，可以直接把"基色桥"这一层折叠进 semantic 层。

## 2. 窗口层：主进程先决定了外观

这一层 v1 完全漏掉了，但它决定了"看起来像不像一个桌面工具"。`main-bootstrap.js` 的 `buildMainWindowOptions`：

```js
{
  ...savedState.bounds,              // 位置尺寸记忆
  minWidth: 800, minHeight: 600,
  show: false, title: app.name,
  // macOS
  acceptFirstMouse: true,
  titleBarStyle: 'hiddenInset',
  trafficLightPosition: <计算值>,
  vibrancy: 'under-window', visualEffectState: 'active',
  backgroundColor: isDark ? '#1e1e1e' : '#ffffff',
  // Windows
  frame: false,
  titleBarOverlay: { isDark, height: 30 },   // MENUBAR_HEIGHT
  backgroundMaterial: <可选> ?? backgroundColor,
  // Linux
  frame: false,
}
```

要点：

| 项 | 值 | 复刻含义 |
| --- | --- | --- |
| 最小窗口 | 800 × 600 | 低于这个尺寸布局会崩；ResumeFlow 若做桌面壳应沿用 |
| 窗口底色 | `#ffffff` / `#1e1e1e` | 比页面底 `#f2f2f2` 更亮，是"窗口纸面"而非"工作区" |
| Windows 原生按钮区 | `titleBarOverlay` 高 **30**（子窗 48） | 顶栏不是纯自绘：Windows 用 WCO，系统按钮浮在渲染层之上 |
| macOS | `hiddenInset` + 红绿灯内缩、`vibrancy:'under-window'` | 侧栏的半透明灰感来自**窗口级毛玻璃**，不是 `backdrop-filter` 描出来的 |
| Splash | 800 × 500 无边框 `#ffffff` | 启动窗与主窗不同宽高，无边框无进度条槽 |
| 主进程 → 渲染层注入 | `?windowTheme` `?colorScheme` `?windowBackgroundLight/Dark` `?windowTitleBarHeight` `?windowLoadingShell` `?windowShellControls=native\|custom` `?windowControls=close,minimize,…` | 表现层参数由主进程下发，渲染层只消费 |

**标题栏高度是三级来源，不是一个数**：

```
:root                       --wb-titlebar-height:56px; --wb-titlebar-height-compact:40px
body[data-electron-desktop] --wb-titlebar-height:64px
运行时                      主进程 ?windowTitleBarHeight 覆盖（Windows WCO 主窗 30，子窗默认 48）
```

macOS 还要给红绿灯让位：`--wb-mac-traffic-light-safe-area` 取 73/79px，offset 计算为 `calc(safe-area - 16px + 18px)`。

## 3. 颜色

### 3.1 原始色阶（节选，light）

| 组 | 值 |
| --- | --- |
| black 10→100 | `#0000001a` `#0003` `#0000004d` `#0006` `#00000080` `#0009` `#000000b3` `#000000bf` `#000000e6` `#000` |
| white 10→100 | `#ffffff1a` `#fff3` `#ffffff4d` `#fff6` `#ffffff80` `#fff9` `#ffffffb3` `#ffffffbf` `#fffc` `#ffffffe6` `#fff` |
| gray 1→5 | `#fafafa` `#f7f7f7` `#f2f2f2` `#ebebeb` `#e6e6e6` |
| **brand 1→10** | `#dff7f2` `#bff0e6` `#9fe8d9` `#80e1cd` `#60d9c0` `#40d1b3` `#00c29a` `#009273` `#00614d` |
| blue 7/8 | `#3370ff` `#2563eb`（另有 `--wb-palette-brand-blue:#0052d9`） |
| red 7/8 | `#e85c5c` `#f64041` |
| green 7/8 | `#29c770` `#0cbf5b` |
| orange 7/8 | `#ff8420` `#ff7800` |
| purple 7/8 | `#7b61ff` `#7c3aed` |

暗色不是简单反相：brand-8 变成 `#4cf0ce`（更亮），gray 变成 `#141414 / #292929 / #1f1f1f / #242424 / #3a3a3a`。

### 3.2 语义层对照

| 语义 | light | dark |
| --- | --- | --- |
| 品牌主色 `brand-8` | `#00c29a` | `#4cf0ce` |
| 品牌浅底 | `#00c29a1f` | — |
| 页面底 `--wb-home-bg-primary` | `#f2f2f2` | `#1f1f1f` |
| 内容底 `--wb-home-bg-secondary` | `#fff` | `#141414` |
| 卡片底 `--wb-bg-primary` | `#fff` | `#1f1f1f` |
| 窗口底（BrowserWindow） | `#ffffff` | `#1e1e1e` |
| 文字 primary | `#000` | — |
| 文字 secondary | `#000000b3` | — |
| 文字 tertiary | `#00000080` | — |
| 文字 disabled / placeholder | `#0000004d` / `#8a9099` | — |
| 边框 primary / secondary | `#e6e6e6` / `#ebebeb` | — |
| 危险 / 成功 / 警告 | `#f64041` / `#0cbf5b` / `#ff7800` | — |

### 3.3 聊天面（`--cr-*`）

| 语义 | light | dark |
| --- | --- | --- |
| canvas | `#fafafa` | `#141414` |
| bg-primary | `#fff` | `#1a1b1e` |
| bg-primary-default | `#f2f2f2` | `#2a2c31` |
| bg-secondary / tertiary | `#f7f7f7` / `#ebebeb` | `#141518` / `#242424` |
| border default / strong | `#ebebeb` / `#e6e6e6` | `#2a2c31` / `#34373d` |
| 用户气泡底 | `#f2f2f2` | `#ffffff1a` |
| 发送按钮填充 | `#000000e6` | `#ffffffeb` |
| 品牌 primary | `#00c29a` | `#3dd9b6` |
| 输入框描边（渐变两端） | `#ebebeb → #f2f2f2` | `#3a3c42 → #2e3036` |

**一个容易被忽略的风格点**：主按钮不是品牌绿，而是近黑 `#000000e6`（`--wb-button-primary-bg: --wb-palette-black-90`）。绿色只用于链接、状态、选中勾、进度等强调位。这是 WorkBuddy 看起来"克制、像工具"而非"像营销页"的主因。

## 4. 字体与排版

| 项 | 值 |
| --- | --- |
| 基础（body） | `var(--vscode-font-family, "PingFang SC", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", sans-serif)`，`font-size` 兜底 **13px**，`color:#3b3b3b`，`-webkit-font-smoothing:antialiased` |
| 聊天面 | `"PingFang SC", -apple-system, "Helvetica Neue", Arial, "Microsoft Yahei", sans-serif`（`--cr-font-family`） |
| 部分 wb 组件 | `Inter, sans-serif` / `SF Pro Text, -apple-system, BlinkMacSystemFont, sans-serif` |
| 等宽 | `Menlo, Monaco, "Courier New"`（`--vscode-editor-font-family`，14px）；代码块另有 `ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas` |
| 字号阶梯 | 8 / 10 / 12 / 13 / 14 / 16 / 18 / 20 / 24 / 28 / 32 |
| 行高阶梯 | 16 / 18 / 20 / 22 / 24 / 28 / 32 / 40 |
| 字重 | 400 / 500 / 600 / 700（无 300） |

**注意 13 与 14 并存**：body 继承 IDE 的 13px，但语义 token 的 `body` 是 14/22。前者是"IDE 外壳基线"，后者是"产品界面基线"，聊天流再上一档到 15/22。复刻时**统一取 14 作正文**即可，不要被 13 带偏。

语义字号（`--wb-font-*`）：

| 角色 | size / line-height / weight |
| --- | --- |
| display | 32 / 40 / 700 |
| h1 | 24 / 32 / 600 |
| h2 | 20 / 28 / 600 |
| h3 | 16 / 24 / 600 |
| body | 14 / 22 / 400 |
| body-strong | 14 / 22 / 600 |
| caption | 12 / 18 / 400 |
| code | 13 / 20 / 400 |

聊天流另有一套 `--wb-font-chatflow-*`：h1 21/37、h2 19/33、h3 17/30、p1 15/22、p2 13/23、p3 11/19 —— 比正文大一档、行高更松（≈1.75），这是对话区"读起来不累"的来源。

Markdown 排版实测：

```
.cr-markdown      { font-size:16px; line-height:1.75 }
.cr-markdown p    { font-size:.875em(14px); margin:0 0 12px }
.cr-markdown h1   { font-size:1.25em(20px); weight 600; margin:20px 0 16px }
.cr-markdown h2/h3{ font-size:1em(16px);    weight 600; margin:16px 0 4px }
.cr-markdown ul/ol{ font-size:.875em; margin:0 0 16px; padding-left:1.25em }
.cr-markdown li   { margin:0 0 4px }
```

## 5. 尺度

| 组 | 值 |
| --- | --- |
| 间距 | 0 / 2 / 4 / **5** / 8 / 12 / 16 / 20 / 24 / 32 / 40 / 48 / 64（注意有 5px 这一档） |
| 圆角 | 0 / 2 / 4 / 6 / 8 / 12 / 16 / 20 / 24 / 9999 |
| 控件高度 | 24 / 28 / 32 / 40 / 48 / 56 |
| 图标 | 12 / 14 / 16 / 20 / 24 |
| 标题栏 | 56（compact 40 / Electron 桌面 64 / 运行时注入见 §2） |
| 滚动条 | 10px（`index.html` 基础样式），聊天区 8px |

阴影只做"轻浮起"，不做重投影：

```
--wb-shadow-sm  0 1px 2px black/8%
--wb-shadow-md  0 2px 8px black/12%
--wb-shadow-lg  0 4px 16px black/16%
--wb-shadow-xl  0 8px 32px black/20%
--wb-shadow-input  0 16px 32px -8px black/3%, 0 2px 4px -4px black/6%   ← 输入框专用，几乎不可见
--wb-shadow-popup-medium 0 6px 24px -4px black/4%, 0 4px 6px -2px black/6%
```

动效：

```
duration  0.1s / 0.15s / 0.2s / 0.3s / 0.4s（instant/fast/medium/gentle/slow）
easing    standard  cubic-bezier(.4,0,.2,1)
          decelerate cubic-bezier(0,0,.2,1)
          accelerate cubic-bezier(.4,0,1,1)
          emphasized cubic-bezier(.2,0,0,1)
          消息入场  cubic-bezier(.34,1.56,.64,1)   ← 唯一带过冲的曲线
```

## 6. 页面骨架

```
┌─────────────────────────────────────────────────────────┐
│ titlebar 56/64px · 可拖拽（-webkit-app-region:drag）      │
├───────────────┬─────────────────────────────────────────┤
│ conversation  │ main-content（bg = home-bg-secondary）   │
│ -list         │  ├ 消息时间线 .cr-message-list           │
│ · 顶部同高    │  │   content padding-inline 40px         │
│ · 拖拽调宽    │  │   消息行 grid: 16px + minmax(0,1fr)   │
│ · hover 露操作│  │   gap 2px 4px，行间距 20px            │
│               │  └ 输入区 .conversation-input            │
│               │      .cr-input-container r24 / .5px 描边│
│               │      toolbar 32px 胶囊 · 发送键 32px 圆 │
└───────────────┴─────────────────────────────────────────┘
```

### 6.1 布局常量（JS 权威值）

这一组是 v1 缺失的，直接决定"拖动侧栏时的行为"，来自 `contexts-*.js`：

```js
// 侧栏
{ EXPANDED_WIDTH: 280, MIN_WIDTH: 240, MAX_WIDTH: 360,
  COLLAPSE_THRESHOLD: 120,   // 拖窄到 <120 → 自动折叠
  EXPAND_THRESHOLD: 150,     // 拖宽到 >150 → 自动展开（滞回，防抖）
  COLLAPSED_WIDTH: 48 }

// 聊天主区
{ MIN_WIDTH: 400, PREFERRED_WIDTH: 600 }

// 详情面板（右侧抽屉）
{ MIN_WIDTH: 360, DEFAULT_WIDTH: 500, OVERVIEW_ONLY_WIDTH: 318, MAX_WIDTH: Infinity }

// 移动断点
{ BREAKPOINT: 600, CHAT_MIN_WIDTH: 400 }
```

- 布局不是 CSS flex 硬撑，而是一套 grid view 引擎（`resizeView` / `getViewSize` / `isViewVisible`），侧栏折叠走 `leadingInset` 订阅通知主内容平移。
- 详情面板打开时做守恒计算：`主区 + 详情 ≤ 容器 - 侧栏 - inset`，且主区不得低于 400、详情不得低于 360，否则放弃打开而不是压扁。
- 折叠/展开有 **120 → 150 的滞回区间**，这是拖拽手感"不抖"的关键，很多人复刻时会漏。

### 6.2 实测数值

| 位置 | 规则 |
| --- | --- |
| 主区 | `.main-content{background:var(--wb-home-bg-secondary);display:flex;flex-direction:column;height:100%;overflow:hidden}` |
| 侧栏顶栏 | `.conversation-list-topbar{height:var(--wb-titlebar-height);-webkit-app-region:drag;justify-content:flex-end;padding:0 12px}` |
| 时间线 | `.conversation-timeline{--cr-message-list-content-inline-padding:40px}` → `.cr-message-list__content{padding-inline:40px;margin:auto}` |
| 消息行 | `.cr-im-message__body{grid-template-columns:16px minmax(0,1fr);gap:2px 4px}`；虚拟列表项 `padding-bottom:20px`（静态分享态 28px），连续消息组收到 6px |
| 输入容器 | `.cr-input-container{border:.5px solid;background:var(--cr-bg-elevated);box-shadow:var(--cr-shadow-input-container);border-radius:24px;padding-top:12px;transition:box-shadow .15s}` |
| 输入区外框 | `.conversation-input__box{--cb-input-content-radius:22px;z-index:16}` |
| 输入本体 | `.cr-input-box{font-size:14px;line-height:1.5;gap:12px}` |
| 底部工具条 | `.cr-input-toolbar{min-height:32px;padding:12px;gap:8px;justify-content:space-between}` |
| 胶囊按钮 | `.cr-mode-selector__trigger / .cr-model-selector__trigger / .cr-input-footer-item{height:32px;border-radius:999px;padding:0 8px;font-size:13px;gap:2-4px}` |
| 发送键 | `.cr-send-button` icon 32×32，`hover{opacity:.8}` `active{transform:scale(.93)}`，发送中 `opacity:.7`，停止态加 `max-width:28px` 的"停"字标签 |
| 首页 | `.wb-home-page{max-width:848px;padding:0 24px;margin:0 auto;min-height:max(432px,100%);--wb-home-composer-base-height:228px}` |
| 首页输入槽 | `.wb-home-composer__input-slot .cr-input-box__main{background:linear-gradient(180deg,#f0f0f0,#f5f5f5);border-radius:24px;padding:0}` |

首页（欢迎态）结构：`wb-home-page > wb-home-header（居中标题,min-height 40px）+ composer（含 input-slot / footer chips）+ wb-home-prompt-list`。空态的输入框比会话态更"重"：有渐变底、228px 基准高度、下方留 284px 预留区。

## 7. 骨架屏（v1 漏掉的一层）

`index.html` 内联了整块骨架样式，`#skeleton-root{position:fixed;inset:0;z-index:99999}`，**在 React bundle 之前就开始画**，所以冷启动不会白屏：

```css
:root{
  --sk-bg:#ffffff;  --sk-titlebar-bg:#f5f5f5;  --sk-sidebar-bg:#f7f7f8;
  --sk-content-bg:#ffffff;  --sk-statusbar-bg:#f8f8f8;
  --sk-titlebar-h:38px;  --sk-sidebar-w:220px;  --sk-statusbar-h:22px;
  --sk-shimmer-color:rgba(0,0,0,.04);
  --sk-text-placeholder:rgba(0,0,0,.06);
  --sk-border-color:rgba(0,0,0,.06);
}
```

三件事值得抄：

1. **骨架尺寸不等于真实尺寸**（侧栏 220 vs 真实 280，顶栏 38 vs 56）。它是"形似"而非"精确占位"，靠形状暗示结构，避免拆除时跳变太明显。
2. **shimmer 只有 4% 黑**，几乎看不见流动，是"呼吸"不是"加载条"。
3. **设置窗有独立的轻骨架** `.sk-settings-loading`：只给稳定底色 + 可拖拽区 + 轻量加载反馈，不做完整骨架，因为设置页打开极快、做全骨架反而增加拆除成本。

## 8. 组件视觉配方

### Button（`--wb-button-*`）

| 尺寸 | 高度 | 最小宽 | 内边距 | 字号 | 圆角 |
| --- | --- | --- | --- | --- | --- |
| xsmall | 16 | — | — | — | — |
| small | 24 | 40 | 8 | 12 | 6 |
| medium | 32 | 68 | 12 | 14 | 8 |
| large | 48 | — | 32 | 14 | 12 |

变体：`primary`（近黑底白字，hover 到 black-75）/ `secondary`（白底 + `#dedede` 描边）/ `grey`（`#ebebeb` 底）/ `ghost`（透明，hover `#0000000f`）/ `link`（`#1470b4`，无底）/ `danger`（`#e85c5c`）。过渡 `0.1s` + standard easing，几乎"瞬时"，不弹。

### Input

高度 28/32/48，圆角 6/8/12，内边距 x 8/12，边框 `#f2f2f2` → hover `#0000004d` → focus `#0000004d`（**focus 不加蓝色发光环，只加深描边**）。填充态底 `#f2f2f2`。

### Card

圆角 12，padding small 8 / medium 16 / large 24，描边 `#f2f2f2`；`raised` 变体阴影 `0 16px 32px #0000000a`；hover 底 `#f0f0f0`。

### Tag / 状态点

`--wb-status-dot-size:6px`；成功/警告/错误/信息分别取 green-8 / orange-8 / red-8 / brand-8，soft 底为 palette ×1（`#e7f8f0` 等）。

### 工具调用卡（`cr-tool-*`）

`cr-tool-call` / `cr-tool-head` / `cr-collapse` / `cr-tool-diff` / `cr-tool-list-files` / `cr-tool-write` 各自成套。行为统一为「一行摘要 + 可展开细节」，折叠区走 `--wb-collapse-header-*` 的 padding 与 hover 底色，展开用 chevron 旋转，无高度动画。

### Markdown 内的富块

`cr-table-block`（含 `--exceed-width` 横向滚动变体）、`cr-mermaid-block`、`cr-document`、`cr-media-generation`（`max-width:800px`）。表格超宽时不压缩列，改为容器滚动。

## 9. 交互逻辑

| 行为 | 实现观察 |
| --- | --- |
| hover 才出现操作 | 侧栏项操作按钮 `.cb-sidebar-nav__item-action{opacity:0;width:20px;height:20px;transition:all .2s}`；消息 hover 操作 `.cr-im-message__hover-actions`（静态分享态直接 `display:none`） |
| 消息追加 | 内容区 `transition:transform .32s cubic-bezier(.34,1.56,.64,1),opacity` —— 带回弹的入场，是"有生命感"的来源 |
| 发送反馈 | 键位：hover 降透明度 → active 缩到 0.93 → 发送中 0.7 且图标换成停止 |
| 侧栏拖拽 | 120 折叠 / 150 展开的滞回（见 §6.1），折叠后保留 48px 图标条 |
| 详情面板 | 打开时守恒计算，主区 ≥400、详情 ≥360，放不下就不开；overview-only 态 318 |
| 层叠关系 | 用变量显式分层：`--wb-conversation-list-control-layer:1` 起逐级 +1（内容 < 吸顶头 < 批量操作条），不用魔法 z-index |
| 吸顶 | `--wb-conversation-list-sticky-header-layer`，配合 `scrollbar-gutter:stable`（`.cr-message-list`）避免滚动条引起左右抖动 |
| 滚动条 | 基础 10px / 聊天区 8px，thumb `#0003`，hover `#00000052`，轨道透明 |
| 折叠/抽屉 | `--sidebar-motion-duration:.3s`、`--sidebar-next-panel-motion-duration:.24s`，easing 统一 standard |
| 窗口级 | 标题栏 `-webkit-app-region:drag`；Windows 用 WCO 让系统按钮压在渲染层之上；侧栏 chrome 全部 `user-select:none` |
| 快捷键面板 | 独立模块 `keyboard-shortcuts-*`，支持录制、冲突检测、单项重置（`editable` / `bindingLocked` / `voiceModifierOnly` 等标志位） |
| 输入区自适应 | 输入框 `container-type:inline-size`，工具栏用容器查询切换密度 |
| 减少动效 | 存在 `cr-im-message-list--static`（分享/截图态）分支：去掉 hover 操作、行距改 28px、去背景 |

## 10. 技术栈（决定"能不能照抄"的边界）

| 层 | 观察 |
| --- | --- |
| 运行时 | Electron 37.10.3-43；主进程 bundle 为单文件大包（`main/index.js` 303 KB、`main-bootstrap.js` 804 KB） |
| 构建 | Vite + **Rolldown**（产物里有 `rolldown-runtime-*.js`），`__vite__mapDeps` 分块 |
| 渲染 | React + react-dom，`contexts-*.js` 是 7 MB 的巨型共享 chunk |
| UI 组件 | **TDesign**（`t-*`），业务层自绘 BEM |
| 图标 | lucide（`createLucideIcon` + 按图标分包，如 `chevron-down-*.js`、`ellipsis-*.js`） |
| 内容渲染 | react-markdown + remark-parse/remark-math + KaTeX + mermaid |
| 浮层 | floating-ui（`floating-ui.react-dom-*.js`） |
| 编辑器 | Monaco（`editor.api2-*.js` 3.9 MB）、`ts.worker` |
| 其他 | jszip（导出）、mitt（事件总线）、classnames/clsx、i18n（`useI18n` + 按语言分包 `zh-cn-*` / `ja-jp-*` / `ko-kr-*`） |
| 源码组织 | monorepo：`packages/agent-ui`（UI 组件与 welcome 页）、`packages/workbuddy-app`（`window-manager.ts`、`shared/account-snapshot.ts`、`title-bar-overlay.ts`） |
| 路由面 | 静态只有 1 条候选 `/experts` → **单壳应用**，页面切换靠内部状态而非路由（ResumeFlow 用 vue-router 时不必照搬） |

## 11. 在 ResumeFlow 里怎么落地

ResumeFlow 现状：Vue 3 + Tailwind **v4**（`@import "tailwindcss"`，无 config 文件）+ radix-vue + lucide。与 WorkBuddy 的 TDesign 不同，但 token 层可以一比一移植。

**建议四层推进**：

1. **Token 层**：`--wb-*` 子集映射进 Tailwind v4 `@theme`，见 `frontend/src/styles/wb-theme.css`。`data-theme="dark"` 挂在 `<html>`，与 WorkBuddy 一致。**跳过 VS Code 基色桥**，直接把窗口底色并进 semantic 层。
2. **骨架层**：对话页改成「顶栏 56 + 左栏可拖拽（280 / 240–360 / 折叠 48）+ 主区 flex」；消息流 `grid-template-columns:16px minmax(0,1fr)`；输入区 24 圆角 + 32 胶囊工具栏。侧栏拖拽务必实现 120/150 滞回。
3. **状态层**：补骨架屏（220 侧栏 + 38 顶栏的"形似"版本，4% shimmer），冷启动与路由切换不再白屏。
4. **组件层**：先用现有组件改 token，不要为了复刻引入 TDesign —— 唯一值得新增的是「工具调用卡」的可折叠头（`cr-tool-head` 的形态）。

**必须改的三个反差点**（不改就"只是换了个绿"）：

- 主按钮从蓝色/品牌色改为近黑 `#000000e6`，绿色只做强调。
- 全局字号下移一档：正文 14 / 行高 22，标题 16–24，信息密度立刻贴近。
- 圆角分档严格执行 6/8/12/24，禁止遍地 `rounded-xl`（与现有 `design-system.md` 的约束一致）。

**风险**：

- 中文字体栈以 PingFang 开头，Windows 会落到 Microsoft YaHei，字重 500/600 观感差异明显，需要显式给中文补 `font-weight:600` 而不是 700。
- `#00c29a` 在白底上对比度约 2.4:1，**只能用于图形与状态点，不能当正文链接色**（WorkBuddy 自己也把链接色设成 `#1470b4`）。
- 若沿用现有 `--color-brand-*` 变量名，注意与 Tailwind v4 `@theme` 命名空间冲突，建议统一加 `wb-` 前缀。
- 深色模式色阶（`#141414` / `#1a1b1e` / `#2a2c31`）与浅色不是同一套 L 值，直接反相会脏。
- macOS 的"高级灰"很大程度来自 `vibrancy:'under-window'` 毛玻璃。ResumeFlow 是 Web，**用 `backdrop-filter` 只能近似**，且会显著增加滚动掉帧风险。

## 12. 未确认项

- **运行时未验证**：目标包不暴露 CDP，无法取真实截图、DOM 与计算样式。spring 曲线手感、拖拽阻尼、WCO 实际遮挡高度（Windows 上系统按钮与顶栏内容的避让）都需真机对照。
- macOS 分支（`hiddenInset` + 红绿灯内缩 + vibrancy）只读到配置，未在 mac 上验证观感；`trafficLightPosition` 是运行时计算值（`require_title_bar_overlay.a()`），未展开到常量。
- 侧栏宽度**已在 JS 中找到夹紧常量**（240–360，默认 280），但用户实际拖到的宽度持久化在 localStorage `agent-ui-sidebar-width`，无法从静态资源得知"典型用户宽度"。
- `--wb-*` 共 1000+ 变量，本文只覆盖与界面观感强相关的部分；表单、表格、日期选择器等后台类组件未展开。
- 主题/皮肤系统（`ThemeManager` / `skin-manager`）只在 `index.html` 注释中出现，皮肤 token 集合未分析。
