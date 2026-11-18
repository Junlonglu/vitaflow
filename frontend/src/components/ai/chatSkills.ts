import type { Component } from "vue"
import { Languages, MessageCircle, SquarePen, Stethoscope, Target, Wand2 } from "lucide-vue-next"

/**
 * 内置技能：与 ai-service src/skills.ts 一一对应。
 *
 * 点击即发送 `/名称`，服务端把该技能的做法注入这一轮的系统提示词；
 * 技能正文不在前端，前端只负责「让人看得懂、点得到」。
 * 输入框的斜杠菜单与对话面板的技能入口共用这一份清单，避免两处各写一套名字。
 */
export interface ChatSkill {
  name: string
  description: string
  /** 需要补参数的技能，点击后把 `/名称 ` 填进输入框等用户续写。 */
  argumentHint: string
  icon: Component
}

export const CHAT_SKILLS: ChatSkill[] = [
  { name: "诊断", description: "体检简历，指出最影响通过率的问题", argumentHint: "", icon: Stethoscope },
  { name: "JD匹配", description: "拆 JD、对证据、按岗位定向改写", argumentHint: "<粘贴岗位描述>", icon: Target },
  { name: "重写经历", description: "把一段经历改写成能被追问的条目", argumentHint: "<哪段经历>", icon: SquarePen },
  { name: "润色", description: "只改表达，不动事实与结构", argumentHint: "<模块名，可留空>", icon: Wand2 },
  { name: "翻译", description: "整份中英互译，结构不变", argumentHint: "<目标语言>", icon: Languages },
  { name: "开场白", description: "Boss直聘 / 微信打招呼消息", argumentHint: "", icon: MessageCircle },
]
