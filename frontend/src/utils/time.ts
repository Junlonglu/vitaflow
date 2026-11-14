/**
 * 相对时间格式化（2026-11-15）
 *
 * 收口前有三份实现，规则互不一样：侧栏 7 天外给「10月20日」、简历卡 30 天外给
 * 「2026-10-20」、信息库干脆没有「分钟」这一档；数字与单位之间有的加空格有的不加
 * （「12 小时前」/「12小时前」）。三处并成一份。
 *
 * 规则（写进 docs/design-system.md §3.10）：
 * - 60 秒内 → 刚刚（可加 suffix，如「刚刚更新」）
 * - 1 小时内 → N 分钟前
 * - 24 小时内 → N 小时前
 * - 48 小时内 → 昨天
 * - 7 天内 → N 天前
 * - 更早 → 当年 `M月D日`，跨年 `YYYY年M月D日`
 * - 数字与单位之间**加一个空格**（与「3 份」「12 次」一致）
 * - 未来时间（时钟偏差）按「刚刚」处理，不出现「-3 分钟前」
 */

const MINUTE = 60 * 1000
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

/** 后端返回的是不带时区的 "2026-11-14 12:00:00"，Safari 直接 new Date 会 NaN */
function parseServerTime(value: string): Date | null {
  const normalized = value.includes("T") ? value : value.replace(" ", "T")
  const date = new Date(normalized)
  return Number.isNaN(date.getTime()) ? null : date
}

export function formatRelativeTime(
  value?: string | null,
  options: { empty?: string; suffix?: string } = {},
): string {
  const { empty = "", suffix = "" } = options
  if (!value) return empty
  const date = parseServerTime(value)
  if (!date) return empty

  const now = new Date()
  const diff = now.getTime() - date.getTime()

  if (diff < MINUTE) return `刚刚${suffix}`
  if (diff < HOUR) return `${Math.floor(diff / MINUTE)} 分钟前`
  if (diff < DAY) return `${Math.floor(diff / HOUR)} 小时前`
  if (diff < 2 * DAY) return "昨天"
  if (diff < 7 * DAY) return `${Math.floor(diff / DAY)} 天前`

  const month = date.getMonth() + 1
  const dayOfMonth = date.getDate()
  return date.getFullYear() === now.getFullYear()
    ? `${month}月${dayOfMonth}日`
    : `${date.getFullYear()}年${month}月${dayOfMonth}日`
}
