/**
 * AI 输入框（composer）视觉规格的唯一来源。
 *
 * 同一个产品里有两个入口用它：工作台首页（WorkspaceHome，说一句话就开新简历）
 * 与简历内 AI 对话（ResumeAiChatPanel，接着一段会话改简历）。两者的功能不同，
 * 但外壳、字体、内边距、发送键必须逐像素一致——否则同一个控件会出现两种样子。
 *
 * 功能差异只能用 props 表达：首页传 `showSessionActions=false`（还没有会话，
 * 就不该有「新对话 / 清空」），其余（模型选择、图片附件、斜杠技能、Enter/IME、
 * 发送键）两端完全一致。改这里两端同时生效，不要在任何一端另写一份。
 */

/** 外壳：16px 圆角 + 极轻投影 + 聚焦时边框加深并加一圈 4% 灰环。 */
export const COMPOSER_SHELL_CLASS =
  "rounded-2xl border border-zinc-200 bg-white shadow-[0_1px_2px_rgba(24,24,27,0.04)] transition-colors focus-within:border-zinc-300 focus-within:ring-2 focus-within:ring-zinc-900/[0.04]"

/**
 * 文本域：内边距由自己承担（外壳不再加 padding），保证文字到边框的距离两端一致。
 *
 * 字号/内边距在这里统一放大到 14→15px：此前 13px 字 + 单行高度放在这么宽的列里显小，
 * 和 30px 的标题摆在一起比例失衡（2026-11-24 由截图反馈调整）。
 */
export const COMPOSER_INPUT_CLASS =
  "block w-full resize-none border-0 bg-transparent px-4 pt-3.5 pb-1 text-[14px] leading-relaxed text-zinc-800 outline-none placeholder:text-zinc-400 focus-visible:ring-0 sm:px-5 sm:pt-4 sm:text-[15px]"

/** 工具条：左侧放附件/模型，右侧放动作与发送键。 */
export const COMPOSER_TOOLBAR_CLASS = "flex items-center gap-1 px-2.5 pb-2.5 pt-1 sm:px-3"

/** 发送键：36px 圆形；空输入为浅灰底灰箭头，有内容才转近黑。 */
export const COMPOSER_SEND_CLASS =
  "flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-wb-primary text-white transition-colors duration-100 hover:bg-wb-primary-hover active:scale-[0.94] disabled:cursor-not-allowed disabled:bg-zinc-200 disabled:text-zinc-400 disabled:active:scale-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-900 focus-visible:ring-offset-2"

/** 工具条里的图标按钮尺寸，与发送键同为 36px，避免同一行出现三种高度。 */
export const COMPOSER_TOOL_BUTTON_CLASS =
  "flex h-9 w-9 shrink-0 items-center justify-center rounded-lg transition-colors active:scale-95 disabled:opacity-40"
