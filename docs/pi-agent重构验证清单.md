# VitaFlow pi-agent 重构 · 验证清单

> 对应 `.scratch/pi-agent-refactor/` 系列票（01-10）的实际验证。faux 模式下 AI 回复为确定性占位文案，属设计内行为；真实模型验证见文末。

## 启动全栈

```bash
# 1. 启动 Docker Desktop（手动打开），等待就绪

# 2. 一键拉起 mysql / redis / minio / ai-service（容器）
docker compose up -d

# 3. 数据库迁移（有新迁移时才需要）
cd backend && ./.venv/Scripts/alembic.exe upgrade head

# 4. 启动后端（端口 8000）
cd backend
./.venv/Scripts/uvicorn.exe app.main:app --reload --host 0.0.0.0 --port 8000

# 5. 启动前端（端口 5173）
cd frontend && npm run dev
```

> 令牌要求：根目录 `.env` 与 `backend/.env` 的 `AI_SERVICE_TOKEN` 必须一致（必填，缺失/不匹配一律 401）。

## A. 基础与鉴权

- [ ] 打开前端首页正常渲染，登录已有账号（或注册新号——注册需要 SMTP 验证码，未配邮件服务时用已有账号）
- [ ] 管理后台 → AI 配置：表单里有「pi 模型引用（pi_model_ref）」字段（迁移 0028 生效）
- [ ] 终端执行 `curl -s -X POST http://127.0.0.1:8300/v1/chat -H "content-type: application/json" -d '{}'` → 返回 `{"type":"error","message":"未授权：需要有效的服务令牌"}`（内网鉴权生效）

## B. 对话改简历（核心链路，faux 确定性演示）

- [ ] 打开一份简历 → 进入 AI 对话，输入 **「把地点改成深圳」**
- [ ] 观察：流式回复出现；Agent 触发字段级工具（后台日志可见 `/api/ai/service/tool/update-basics-field` 回调）
- [ ] 出现**待确认变更卡片** → 点「应用」→ 回到编辑器确认地点已变为深圳，刷新页面仍为深圳（持久化）
- [ ] 再试一次，这次点「拒绝」→ 简历不被修改

## C. 快捷指令

- [ ] 输入 `/润色 工作经历` → 生成待确认方案（改措辞不改事实）→ 应用/拒绝都走通
- [ ] 输入 `/优化 <粘贴一段JD>` → faux 演示走「读模块 → propose_change」两步工具链
- [ ] 输入 `/生成 后端工程师` → 生成待确认方案
- [ ] 输入 `/翻译` → 整份中英互译待确认

## D. 一次性任务（ticket 09 迁移项）

- [ ] 简历列表页「AI 智能生成」→ 填目标岗位生成 → 流式出结果、能正常建简历（走 ai-service `POST /v1/task`）
- [ ] 文件/图片导入简历 → 解析出占位简历（faux）

## E. 多模态图片

- [ ] 对话框上传一张 jpg/png 截图发送 → faux 走「读模块 → propose_change」→ 生成待确认变更

## F. 计费对账（faux 口径）

- [ ] 头像/个人中心 → Flow Points 明细：**B/C/E 的 faux 对话不产生任何流水、不扣点**
- [ ] D 的生成任务有扣点流水（按 `flow_point_rules` 中 `generate_resume` 规则计费）
- [ ] 历史记录页：AI 任务记录正常展示，退役功能（简历评分/JD 优化旧入口）不再出现

## G. 回归确认

- [ ] 简历编辑器手动编辑、保存、导出 PDF/Word 等原有功能不受影响
- [ ] 刷新页面后 AI 会话历史还在（MySQL 为唯一事实源）

## 真实模型验证（需 API Key，faux 覆盖不到的部分）

```bash
cd ai-service
mkdir -p .pi-agent && cp models.json.example .pi-agent/models.json   # 按需改模型列表
# 根目录 .env：PI_PROVIDER=real、DEEPSEEK_API_KEY=<密钥>
cd .. && docker compose up -d ai-service
```

- [ ] B/C/E 的真实语义：模型理解自然语言并正确选工具、图片被真正识别、/优化 真读 JD
- [ ] 按模型计费：对话结束后 Flow Points 流水按 `pi_model_ref` 对应费率入账，与 ai-service 归集的 token 用量对账一致
- [ ] 多模态：vision 模型（`models.json` 中 `input: ["text","image"]`）真实读图生成方案
