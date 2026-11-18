import { beforeEach, describe, expect, it } from "vitest"

import { takeChatHandoff, writeChatHandoff } from "@/utils/chatHandoff"

const KEY = "vitaflow_chat_handoff"

describe("chatHandoff 首页 → 编辑器交接", () => {
  beforeEach(() => sessionStorage.clear())

  it("写进去的文本、附件、模型都能原样读出来", () => {
    writeChatHandoff({
      prompt: "帮我写一份后端实习简历",
      attachments: [{ url: "ai-chat/a.png", name: "a.png", content_type: "image/png", object_name: "a.png" }],
      modelKey: "u:7",
    })

    expect(takeChatHandoff()).toEqual({
      prompt: "帮我写一份后端实习简历",
      attachments: [{ url: "ai-chat/a.png", name: "a.png", content_type: "image/png", object_name: "a.png" }],
      modelKey: "u:7",
    })
  })

  it("读一次就删，刷新不会重复发送", () => {
    writeChatHandoff({ prompt: "你好", attachments: [], modelKey: null })
    expect(takeChatHandoff()).not.toBeNull()
    expect(takeChatHandoff()).toBeNull()
    expect(sessionStorage.getItem(KEY)).toBeNull()
  })

  it("空文本又没附件时不算有效交接", () => {
    writeChatHandoff({ prompt: "   ", attachments: [], modelKey: "p:1" })
    expect(takeChatHandoff()).toBeNull()
  })

  it("坏数据不抛错：字段缺失回落到安全默认值", () => {
    sessionStorage.setItem(KEY, JSON.stringify({ prompt: "只有文本" }))
    expect(takeChatHandoff()).toEqual({ prompt: "只有文本", attachments: [], modelKey: null })

    sessionStorage.setItem(KEY, "{不是 json")
    expect(takeChatHandoff()).toBeNull()
  })
})
