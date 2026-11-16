import request from "./request"

function safeFilename(value: string | undefined, fallback: string) {
  const name = String(value || "").trim() || fallback
  return name.replace(/[\\/:*?"<>|]+/g, "_").replace(/\s+/g, " ").slice(0, 80) || fallback
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

export async function exportPdfApi(id: number, title?: string) {
  const res = await request.post(`/resumes/${id}/export/pdf`, {}, { responseType: "blob", timeout: 120000 })
  downloadBlob((res as any).data, `${safeFilename(title, `vitaflow_resume_${id}`)}.pdf`)
}

/** 导出结构化快照：含标题、语言、模板与样式配置，可再导入回填成一份简历。 */
export async function exportJsonApi(id: number, title?: string) {
  const res = await request.post(`/resumes/${id}/export/json`, {}, { responseType: "blob", timeout: 30000 })
  const data = (res as any).data instanceof Blob ? (res as any).data : new Blob([(res as any).data], { type: "application/json" })
  downloadBlob(data, `${safeFilename(title, `vitaflow_resume_${id}`)}.json`)
}
