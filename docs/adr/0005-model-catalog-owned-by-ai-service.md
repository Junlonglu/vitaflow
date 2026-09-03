# 模型配置事实源迁移到 AI 服务模型目录

status: accepted
date: 2026-09-03

pi-agent 重构完成后（ticket 07–10），后端与模型已彻底解耦：每轮对话只携带一个模型引用（`provider/模型id`）调用 AI 服务，由 AI 服务在 `models.json` 中解析出真正的模型定义与 API Key。但配置面残留着两套并行的「事实源」：管理后台/`ai_model_configs` 表仍保留直连四件套（provider / base_url / api_key / model / temperature / timeout / max_tokens），`backend/.env` 仍保留六项 `AI_*` 直连配置——它们不再参与运行时行为，却继续制造「后端持有模型配置」的假象，且模型目录（models.json）只能手工编辑文件，后台看不见 AI 服务里有什么模型。

我们决定：**模型目录（Model Catalog）的事实源收敛到 AI 服务**——models.json 是唯一记录「有哪些模型、模型能力、API Key」的地方；管理后台通过 AI 服务的目录 CRUD 接口全量管理它（按服务商分组，Key 由后台录入、AI 服务明文写入 models.json）。**模型策略（Model Policy）作为业务事实源保留在 MySQL**——从目录中选定一个模型（`model_ref`，原 `pi_model_ref` 更名）、费率、上下文条数、是否启用；默认模型沿用 is_active + sort_order 启用机制。后端直连残留全部拆除：`.env` 六项、config.py 对应字段、环境变量回退配置、启动种子、api_key 可用性门禁。

## Considered Options

- **维持现状（目录手工编辑文件，后台只管策略）**：目录变化需登服务器改文件，与「后台全管 AI」的运维预期冲突，拒绝。
- **后台全管目录 + Key 存环境变量**：更规范，但后台录入新 Key 需要修改容器环境变量并重启，无法热生效，与全管目标冲突，拒绝。
- **后台全管目录 + Key 明文写 models.json**（采纳）：换 Key 即时生效（ModelRuntime 每请求重建，无需重启）；Key 不进后端 DB 与后端 .env，暴露面收敛在服务器文件权限内；代价是密钥明文落盘，依赖部署环境对 `.pi-agent/` 目录的文件权限保护。

## Consequences

- 模型能力（多模态、上下文窗）以目录声明为准，策略表的 `supports_multimodal` 删除改为派生；`context_messages` 是策略属性（带几条历史），保留可编辑。
- 目录删除保护：被策略引用或启用中的模型拒绝删除，避免悬空引用。
- 策略保存时对 `model_ref` 做降级校验（目录可达则校验存在，不可达放行并告警），运行时报错兜底。
- 数据库零策略时报错「请先在后台配置模型」，不再有环境变量隐式回退。
- faux 模式目录列表返回合成的 `faux/demo` 条目，保证无 Key 回归链路照常可跑。
