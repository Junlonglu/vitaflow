import { describe, expect, it } from "vitest"
import { errorMessage, isAbortError } from "@/utils/errorText"

describe("errorMessage", () => {
  it("后端中文提示原样透传", () => {
    expect(errorMessage(new Error("邮箱已被注册"), "注册失败")).toBe("邮箱已被注册")
  })

  it("英文传输层报错换成兜底文案", () => {
    expect(errorMessage(new Error("Network Error"), "加载失败")).toBe("加载失败")
    expect(errorMessage(new Error("Failed to fetch"), "加载失败")).toBe("加载失败")
  })

  it("中英混排含中文则透传", () => {
    expect(errorMessage(new Error("导出 PDF 失败"), "兜底")).toBe("导出 PDF 失败")
  })

  it("非 Error（字符串 / null / undefined）一律换兜底", () => {
    expect(errorMessage("boom", "兜底")).toBe("兜底")
    expect(errorMessage(null, "兜底")).toBe("兜底")
    expect(errorMessage(undefined, "兜底")).toBe("兜底")
  })

  it("抛出的普通对象也换兜底，不读 message", () => {
    expect(errorMessage({ message: "something" }, "兜底")).toBe("兜底")
  })
})

describe("isAbortError", () => {
  it("识别 AbortError", () => {
    const err = new Error("cancelled")
    err.name = "AbortError"
    expect(isAbortError(err)).toBe(true)
  })

  it("普通 Error 与非 Error 都不是取消", () => {
    expect(isAbortError(new Error("failed"))).toBe(false)
    expect(isAbortError("AbortError")).toBe(false)
    expect(isAbortError(null)).toBe(false)
  })
})
