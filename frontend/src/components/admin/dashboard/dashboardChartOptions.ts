/**
 * 看板图表的 option 构造器。
 *
 * 这些 option 原来写在 `Admin.vue` 里、由页面 computed 出再逐层透传给面板；
 * 看板改成 tab 之后每个 tab 只关心自己的图，构造逻辑收在这里，页面不再持有图表配置。
 */
import type { EChartsOption } from "echarts"
import type { DashboardData } from "@/api/admin"

const AXIS_LABEL = { color: "#a1a1aa" }
const AXIS_LINE = { lineStyle: { color: "#e4e4e7" } }
const SPLIT_LINE = { lineStyle: { color: "#f4f4f5" } }
const TOOLTIP_BASE = {
  borderWidth: 0,
  backgroundColor: "#18181b",
  textStyle: { color: "#fff", fontSize: 12 },
}

export interface TrendSeries {
  key: "users" | "resumes" | "ai_tasks" | "exports" | "shares"
  name: string
  color: string
}

function tooltipShell(title: string, rows: string[]): string {
  return [`<div style="font-weight:700;margin-bottom:6px;">${title}</div>`, ...rows].join("<br/>")
}

/** 多序列图的图例：贴右上，与卡片标题同一行高度 */
const LEGEND = {
  top: 0,
  right: 4,
  itemWidth: 8,
  itemHeight: 8,
  itemGap: 12,
  icon: "roundRect",
  textStyle: { color: "#71717a", fontSize: 11 },
}

/** 按日趋势：多条折线，不再把量纲不同的动作相加成「总活动」。 */
export function buildDailyTrendOption(daily: DashboardData["daily"], series: TrendSeries[]): EChartsOption {
  return {
    color: series.map((item) => item.color),
    legend: { ...LEGEND, data: series.map((item) => item.name) },
    tooltip: {
      trigger: "axis",
      ...TOOLTIP_BASE,
      formatter: (params: unknown) => {
        const rows = Array.isArray(params) ? params : [params]
        const index = (rows[0] as { dataIndex?: number } | undefined)?.dataIndex ?? 0
        const day = daily[index]
        if (!day) return ""
        return tooltipShell(
          day.date,
          series.map((item) => `${item.name}：<b>${Number(day[item.key] || 0)}</b>`),
        )
      },
    },
    grid: { left: 4, right: 8, top: 30, bottom: 14, containLabel: true },
    xAxis: {
      type: "category",
      data: daily.map((item) => item.date),
      axisTick: { show: false },
      axisLine: AXIS_LINE,
      axisLabel: AXIS_LABEL,
    },
    yAxis: { type: "value", minInterval: 1, splitLine: SPLIT_LINE, axisLabel: AXIS_LABEL, axisLine: { show: false }, axisTick: { show: false } },
    series: series.map((item) => ({
      name: item.name,
      type: "line",
      smooth: true,
      showSymbol: daily.length <= 14,
      symbolSize: 6,
      lineStyle: { width: 2 },
      data: daily.map((day) => Number(day[item.key] || 0)),
    })),
  }
}

/** 近 7 天按小时分布：活跃用户折线 + 操作次数柱（双轴）。 */
export function buildHourlyActivityOption(items: NonNullable<DashboardData["hourly_activity"]>): EChartsOption {
  return {
    color: ["#10b981", "#cbd5e1"],
    legend: { ...LEGEND, data: ["活跃用户", "操作次数"] },
    tooltip: {
      trigger: "axis",
      ...TOOLTIP_BASE,
      formatter: (params: unknown) => {
        const rows = Array.isArray(params) ? params : [params]
        const index = (rows[0] as { dataIndex?: number } | undefined)?.dataIndex ?? 0
        const item = items[index]
        if (!item) return ""
        return tooltipShell(item.label, [`活跃用户：<b>${item.active_users}</b> 人`, `操作次数：<b>${item.actions}</b>`])
      },
    },
    grid: { left: 4, right: 4, top: 30, bottom: 18, containLabel: true },
    xAxis: {
      type: "category",
      data: items.map((item) => item.label),
      axisTick: { show: false },
      axisLine: AXIS_LINE,
      axisLabel: { ...AXIS_LABEL, interval: 3, formatter: (value: string) => value.slice(0, 2) },
    },
    yAxis: [
      { type: "value", minInterval: 1, splitLine: SPLIT_LINE, axisLabel: AXIS_LABEL, axisLine: { show: false }, axisTick: { show: false } },
      { type: "value", minInterval: 1, splitLine: { show: false }, axisLabel: AXIS_LABEL, axisLine: { show: false }, axisTick: { show: false } },
    ],
    series: [
      {
        name: "活跃用户",
        type: "line",
        smooth: true,
        showSymbol: false,
        symbolSize: 7,
        data: items.map((item) => item.active_users),
        areaStyle: { color: "rgba(16,185,129,0.08)" },
        lineStyle: { width: 2.5 },
      },
      { name: "操作次数", type: "bar", yAxisIndex: 1, barMaxWidth: 8, itemStyle: { borderRadius: [4, 4, 0, 0] }, data: items.map((item) => item.actions) },
    ],
  }
}
