import { beforeEach, describe, expect, it, vi } from "vitest"
import { computed, defineComponent, h } from "vue"
import { flushPromises, mount } from "@vue/test-utils"

import { sendResumeChatMessageStreamApi } from "@/api/ai"
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

function mountStream() {
  let stream!: ReturnType<typeof useResumeChatStream>
  const Harness = defineComponent({
    setup() {
      stream = useResumeChatStream(createOptions())
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
