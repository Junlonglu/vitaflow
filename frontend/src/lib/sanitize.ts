import DOMPurify from "dompurify"

/**
 * 用户提交的富文本在 `v-html` 之前的统一出口。
 *
 * 后端已经用 bleach 清过一遍，但前端仍要自己兜一次：历史数据、接口变更、
 * 非本服务写入的测试数据都可能带标签进来。Admin / Profile 原来各写了一份。
 */
export function sanitizeFeedbackHtml(content?: string | null): string {
  return DOMPurify.sanitize(content || "")
}
