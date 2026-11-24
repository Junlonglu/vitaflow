import { beforeEach, describe, expect, it, vi } from "vitest"
import { computed, defineComponent, h } from "vue"
import { flushPromises, mount } from "@vue/test-utils"

import { decideResumeChatChangeApi, sendResumeChatMessageStreamApi } from "@/api/ai"
import { useResumeChatStream, type ResumeChatStreamOptions } from "@/composables/useResumeChatStream"

vi.mock("@/stores/chat", () => ({
  useChatStore: () => ({
    activeSessionId: () => 1,
    isLoaded: () => true,
    fetchSessions: vi.fn(async () => undefined),
    ensureSession: vi.fn(async () => ({ id: 1 })),
    createSession: vi.fn(async () => ({ id: 1 })),
    touchSession: vi.fn(),
    setActiveSession: vi.fn(),
  }),
}))

vi.mock("@/api/ai", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/ai")>()
  return {
    ...actual,
    sendResumeChatMessageStreamApi: vi.fn(),
    regenerateResumeChatMessageStreamApi: vi.fn(),
    getResumeChatMessagesApi: vi.fn(),
    clearResumeChatMessagesApi: vi.fn(),
    decideResumeChatChangeApi: vi.fn(),
  }
})

type StreamCallbacksStub = { signal?: AbortSignal }

/** 让 mock 的流式请求像真 fetch 一样响应 abort。 */
function pendingUntilAborted(callbacks?: StreamCallbacksStub) {
  return new Promise((_resolve, reject) => {
    callbacks?.signal?.addEventListener("abort", () => {
      const error = new Error("aborted")
      error.name = "AbortError"
      reject(error)
    })
  })
}

/** mockImplementation 需要显式参数类型，否则 noImplicitAny 会报错。 */
function mockSendStream(handler: (callbacks?: StreamCallbacksStub) => Promise<unknown>) {
  vi.mocked(sendResumeChatMessageStreamApi).mockImplementation(
    ((_resumeId: number, _payload: unknown, callbacks?: StreamCallbacksStub) =>
      handler(callbacks)) as unknown as typeof sendResumeChatMessageStreamApi,
  )
}

function createOptions(): ResumeChatStreamOptions {
  return {
    resumeId: computed(() => 7),
    supportsImages: computed(() => false),
    modelConfigId: computed(() => null),
    userModelId: computed(() => null),
    ensureReady: async () => true,
    flushHistory: () => undefined,
    beforeRequest: async () => undefined,
    applyResumeSnapshot: async () => undefined,
    onPendingQuestions: () => undefined,
    onJdOptimized: () => undefined,
    onPointsUsed: () => undefined,
    onTurnEnd: () => undefined,
  }
}

function mountStream(options: ResumeChatStreamOptions = createOptions()) {
  let stream!: ReturnType<typeof useResumeChatStream>
  const Harness = defineComponent({
    setup() {
      stream = useResumeChatStream(options)
      return () => h("div")
    },
  })
  const wrapper = mount(Harness)
  return { stream, wrapper }
}

describe("useResumeChatStream cancellation", () => {
  beforeEach(() => {
    vi.mocked(sendResumeChatMessageStreamApi).mockReset()
  })

  it("aborts the in-flight turn when the component unmounts", async () => {
    let signal: AbortSignal | undefined
    mockSendStream((callbacks) => {
      signal = callbacks?.signal
      return pendingUntilAborted(callbacks)
    })

    const { stream, wrapper } = mountStream()
    void stream.send("帮我看看简历")
    await flushPromises()
    expect(signal?.aborted).toBe(false)

    wrapper.unmount()
    await flushPromises()

    expect(signal?.aborted).toBe(true)
    expect(stream.error.value, "主动取消不该弹错误").toBe("")
  })

  it("aborts and clears state when switching resumes", async () => {
    let signal: AbortSignal | undefined
    mockSendStream((callbacks) => {
      signal = callbacks?.signal
      return pendingUntilAborted(callbacks)
    })

    const { stream } = mountStream()
    void stream.send("帮我把项目经历写具体一点")
    await flushPromises()

    stream.reset()
    await flushPromises()

    expect(signal?.aborted).toBe(true)
    expect(stream.messages.value).toEqual([])
    expect(stream.loading.value).toBe(false)
    expect(stream.error.value).toBe("")
  })

  it("clears the abort controller after a normal turn so later turns are unaffected", async () => {
    const signals: (AbortSignal | undefined)[] = []
    mockSendStream((callbacks) => {
      signals.push(callbacks?.signal)
      return Promise.resolve({ assistant_message: { id: 2, content: "好的" } })
    })

    const { stream } = mountStream()
    await stream.send("第一句")
    await stream.send("第二句")

    expect(signals[0]?.aborted).toBe(false)
    expect(signals[1]).toBeDefined()
    expect(signals[1]).not.toBe(signals[0])
    expect(stream.error.value).toBe("")
  })
})

describe("isAbortError wiring", () => {
  it("treats a signal-less request as normal", async () => {
    mockSendStream((callbacks) => {
      expect(callbacks?.signal).toBeDefined()
      return Promise.resolve({ assistant_message: { id: 3, content: "ok" } })
    })

    const { stream } = mountStream()
    await stream.send("你好")

    expect(stream.messages.value.length).toBeGreaterThan(0)
  })
})

describe("decide · 应用提案的合并基准", () => {
  const pendingMessage = (optimized: unknown) => ({
    id: 5,
    role: "assistant" as const,
    content: "",
    action_status: "pending" as const,
    optimized_resume_data: optimized,
  })

  beforeEach(() => {
    vi.mocked(decideResumeChatChangeApi).mockReset()
    vi.mocked(decideResumeChatChangeApi).mockResolvedValue({
      assistant_message: { id: 5, action_status: "applied" },
    } as any)
  })

  it("主按钮没带数据时，现场把提案合并进当前简历再提交", async () => {
    const optimized = { basics: { name: "AI 版" } }
    const merged = { basics: { name: "合并版" } }
    const buildMergedResumeData = vi.fn(() => merged)
    const { stream } = mountStream({ ...createOptions(), buildMergedResumeData })
    stream.messages.value = [pendingMessage(optimized)]

    await stream.decide(stream.messages.value[0], "apply")

    expect(buildMergedResumeData).toHaveBeenCalledWith(optimized)
    // 第 4 个参数就是 merged_resume_data：传了它，后端才不走整包快照
    expect(vi.mocked(decideResumeChatChangeApi).mock.calls[0][3]).toEqual(merged)
  })

  it("审阅台显式传入的逐条勾选结果优先，不再二次合并", async () => {
    const explicit = { basics: { name: "只勾了一条" } }
    const buildMergedResumeData = vi.fn(() => ({ basics: { name: "不该用到" } }))
    const { stream } = mountStream({ ...createOptions(), buildMergedResumeData })
    stream.messages.value = [pendingMessage({ basics: { name: "AI 版" } })]

    await stream.decide(stream.messages.value[0], "apply", explicit)

    expect(buildMergedResumeData).not.toHaveBeenCalled()
    expect(vi.mocked(decideResumeChatChangeApi).mock.calls[0][3]).toEqual(explicit)
  })

  it("驳回与「只有信息库提案」都不触发合并", async () => {
    const buildMergedResumeData = vi.fn(() => ({ basics: {} }))
    const { stream } = mountStream({ ...createOptions(), buildMergedResumeData })
    stream.messages.value = [pendingMessage({ basics: { name: "AI 版" } })]

    await stream.decide(stream.messages.value[0], "reject")
    stream.messages.value = [{ ...pendingMessage(undefined), id: 6 }]
    await stream.decide(stream.messages.value[0], "apply")

    expect(buildMergedResumeData).not.toHaveBeenCalled()
    expect(vi.mocked(decideResumeChatChangeApi).mock.calls[1][3]).toBeUndefined()
  })
})
