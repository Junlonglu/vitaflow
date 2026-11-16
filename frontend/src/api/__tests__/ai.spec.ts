import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { generateResumeStreamApi, isSilentStreamError } from "@/api/ai"
import { showGlobalToast } from "@/utils/toast"

vi.mock("@/utils/toast", () => ({ showGlobalToast: vi.fn() }))

function jsonResponse(status: number, body: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    body: {},
    headers: new Headers({ "content-type": "application/json" }),
    text: async () => JSON.stringify(body),
  } as unknown as Response
}

function textResponse(status: number, body: string, contentType = "text/html") {
  return {
    ok: status >= 200 && status < 300,
    status,
    body: {},
    headers: new Headers({ "content-type": contentType }),
    text: async () => body,
  } as unknown as Response
}

describe("postAiStream error handling", () => {
  beforeEach(() => {
    localStorage.clear()
    vi.mocked(showGlobalToast).mockClear()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it("treats 402 as an already-reported error, regardless of wording", async () => {
    // 关键点：判定不依赖「点数」「Flow Points」这类文案。
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse(402, { message: "Insufficient balance" })),
    )

    const error = await generateResumeStreamApi({}).catch((err) => err)

    expect(showGlobalToast).toHaveBeenCalledWith("Insufficient balance", "error")
    expect(isSilentStreamError(error)).toBe(true)
    expect(error.message).toBe("Insufficient balance")
  })

  it("keeps ordinary 4xx errors as-is without a global toast", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse(400, { message: "简历内容不完整" })),
    )

    const error = await generateResumeStreamApi({}).catch((err) => err)

    expect(showGlobalToast).not.toHaveBeenCalled()
    expect(isSilentStreamError(error)).toBe(false)
    expect(error.message).toBe("简历内容不完整")
  })

  it("reads detail from validation errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse(422, { detail: "字段格式不正确" })),
    )

    const error = await generateResumeStreamApi({}).catch((err) => err)

    expect(error.message).toBe("字段格式不正确")
  })

  it("does not leak a gateway HTML page to the user", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(textResponse(502, "<html><body>Bad Gateway</body></html>")),
    )

    const error = await generateResumeStreamApi({}).catch((err) => err)

    expect(error.message).toBe("AI 请求失败")
  })

  it("reports a JSON body returned with 200 as an error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse(200, { message: "当前没有可用的模型" })),
    )

    const error = await generateResumeStreamApi({}).catch((err) => err)

    expect(error.message).toBe("当前没有可用的模型")
    expect(showGlobalToast).not.toHaveBeenCalled()
  })
})
