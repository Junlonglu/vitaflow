import { describe, expect, it } from "vitest"
import { avatarObjectName, avatarSrc } from "@/utils/avatar"

describe("avatarSrc", () => {
  it("把对象名拼成可渲染的相对地址", () => {
    expect(avatarSrc("avatar/1_a.jpg")).toBe("/api/files/avatar/1_a.jpg")
  })

  it("完整 URL 原样返回（旧数据与本系统外链）", () => {
    expect(avatarSrc("http://127.0.0.1:8000/api/files/avatar/1_a.jpg")).toBe(
      "http://127.0.0.1:8000/api/files/avatar/1_a.jpg",
    )
    expect(avatarSrc("https://cdn.example.com/me.png")).toBe("https://cdn.example.com/me.png")
  })

  it("空值返回空串，避免渲染出 img 破图", () => {
    expect(avatarSrc("")).toBe("")
    expect(avatarSrc(null)).toBe("")
    expect(avatarSrc(undefined)).toBe("")
  })
})

describe("avatarObjectName", () => {
  it("能从三种形态里取出对象名", () => {
    expect(avatarObjectName("avatar/1_a.jpg")).toBe("avatar/1_a.jpg")
    expect(avatarObjectName("/api/files/avatar/1_a.jpg")).toBe("avatar/1_a.jpg")
    expect(avatarObjectName("http://127.0.0.1:8000/api/files/avatar/1_a.jpg?t=1")).toBe("avatar/1_a.jpg")
  })

  it("外链与非法值取不出对象名", () => {
    expect(avatarObjectName("https://cdn.example.com/me.png")).toBe("")
    expect(avatarObjectName("/api/files/announcement/x.jpg")).toBe("")
    expect(avatarObjectName("data:image/png;base64,AAA")).toBe("")
    expect(avatarObjectName("")).toBe("")
  })
})
