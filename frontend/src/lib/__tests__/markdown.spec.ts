import { describe, expect, it } from "vitest"
import { localizeAiText, renderMarkdown } from "@/lib/markdown"

describe("localizeAiText", () => {
  it("把 AI 输出里的内部字段名换成中文标签", () => {
    expect(localizeAiText("已更新 skills 与 projects")).toBe("已更新 专业技能 与 项目经历")
  })

  it("不误伤同前缀的长字段名", () => {
    // optimized_resume_data 更长，应先于 resume_data 命中
    expect(localizeAiText("optimized_resume_data 已生成")).toBe("优化后的简历内容 已生成")
    expect(localizeAiText("resume_data 已生成")).toBe("简历内容 已生成")
  })

  it("标识符内部不替换", () => {
    expect(localizeAiText("resume_data_backup")).toBe("resume_data_backup")
    expect(localizeAiText("my_skills_list")).toBe("my_skills_list")
  })

  it("大小写不敏感，且保留原有语法结构", () => {
    expect(localizeAiText("`Skills` 字段")).toBe("`专业技能` 字段")
    expect(localizeAiText("**highlights**")).toBe("**亮点**")
  })

  it("空输入返回空串", () => {
    expect(localizeAiText()).toBe("")
    expect(localizeAiText("")).toBe("")
  })
})

describe("renderMarkdown", () => {
  it("渲染基础 Markdown", () => {
    const html = renderMarkdown("**加粗** 和 `code`")
    expect(html).toContain("<strong>加粗</strong>")
    expect(html).toContain("<code>code</code>")
  })

  it("保留列表结构", () => {
    const html = renderMarkdown("- 一\n- 二")
    expect(html).toContain("<ul>")
    expect(html).toContain("<li>一</li>")
  })

  it("剥掉脚本与事件属性", () => {
    const html = renderMarkdown('<script>alert(1)</script><img src=x onerror="alert(1)">')
    expect(html).not.toContain("<script")
    // 危险标记只能以转义文本出现，不能生成真实标签
    expect(html).not.toMatch(/<img[^>]*onerror/i)
    expect(html).toContain("&lt;script&gt;")
  })

  it("禁止 javascript: 链接", () => {
    const html = renderMarkdown("[x](javascript:alert(1))")
    expect(html).not.toContain('href="javascript:')
  })

  it("空输入不报错", () => {
    expect(renderMarkdown()).toBe("")
  })
})
