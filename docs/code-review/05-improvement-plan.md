# Phase 6：问题归因与整体改进方案

> 输入：`code-review/00`～`04` 四份专项 + `code-review-report.md`（66 条 `RF-xxx`）。
> 本文不重复缺陷描述，只做三件事：**把 66 条收敛成可治理的机制**、**给出分层的改进方案与验收标准**、**指出审查本身留下的空白**。
> 基线：`a8d451c`（2026-11-20）。本文为方案设计，不含代码改动。

---

## 0. 一句话结论

这套系统的问题不是「某个模块写得糙」，而是**六个横切机制没有建立**：谁最后写、这笔钱属于哪一轮、中间态谁收尾、参数从哪里取值、正确性怎么自证、谁说自己是自己。

66 条缺陷里约 **40 条**是这六个机制的直接投影，剩下 **26 条**才是可以按迭代修掉的独立点。所以正确的动作不是「按 P0→P3 顺序修 66 个 bug」——那会在修完第 20 条时重新长出第 21 条——而是**先把机制立起来，让一片缺陷同时失去存在条件**，再用常规迭代清理剩余独立点。

---

## 1. 先修正三个前提

### 1.1 66 条的可信度不是同一档

报告自述的口径是「已确认＝代码路径成立，不代表运行环境观测到」。按这个口径重新分层：

| 档位 | 判定依据 | 对方案的影响 |
| --- | --- | --- |
| 代码路径确定（多数） | 静态读代码即可复现 | 可直接排期 |
| 高可信（RF-005/009/053 等） | 需特定时序/负载 | 必须先造场景复现再改，否则改完无法验收 |
| 待验证（约 10 条） | 需运行时证据 | 优先级**悬空**，不能进第一周 |
| 部署条件触发（RF-003/008/011/052） | 取决于生产配置与访问方式 | 决定 P0 名单是否成立 |

**这是方案的第一条纪律：不先定格事实，任何路线图都是猜的。** 4 条部署条件 + 10 条待验证，半天可以做完（§4）。

### 1.2 66 条不是 66 个问题

按根因归并（详细映射见 §2）：

| 机制 | 条数 | 编号 |
| --- | --- | --- |
| M1 写入语义（谁是最后写入者） | 8 | 001/002/004/005/006/013/030/057 |
| M2 回合身份（这笔钱/这次改动属于哪一轮） | 5 | 007/014/018/019/040 |
| M3 生命周期与幂等（中间态谁收尾） | 8 | 009/015/016/028/031/042/058/065 |
| M4 参数与契约的单一来源 | 5 | 033/036/044/052/053 |
| M5 可证明性（能不能自证） | 5 | 017/034/061/062/066 |
| M6 信任模型（谁说自己是自己） | 9 | 003/008/012/025/027/037/055/056/059 |
| 独立点（一次性修复） | 26 | 其余 |

### 1.3 审查流程本身有缺口，而它解释了为什么会有 15 条「新」问题

- **Phase 1 迟到**：`01-backend-review.md` 在最终审计之后才产出，补查后新增 15 条（含 3 条 P1）。说明专项不是有 gate 的流水线，而是并行拼装——**最贴近凭据与资金的后端非 AI 链路，长期没有同等级审查**。RF-052（分享页裂图）、RF-036（复制必 500）、RF-056（脱敏不全）都不是难问题，是覆盖问题。
- **编号体系五套**（`B-xx`/`C-xx`/`F-xx`/`L-xx`/`RF-xxx`）＋ 约 330KB 文档 ＋ 一张人工维护的合并表。这份产物**本身不可持续**：下一轮 review 要重新对齐编号，而不是接着往下审。
- **没有验证日**：报告明确「未做运行时复现」，于是 14 条结论停在静态推断，路线图的第一优先级建立在这 14 条之上。
- **四个方向没被覆盖**：依赖 CVE 与镜像基础层、**备份与恢复演练**（25 表无外键、对象存储删除只清索引——这是最需要演练的形态）、容量规划、**个人信息合规**（简历即 PII，公开分享 + 脱敏不全的组合在合规上是问题，不只是技术问题）。

→ 对应到方案：§8 把「审查流水线」当成和代码同等的交付物来治。

---

## 2. 六个机制：现象、本质、以及「只修 bug 会怎样」

### M1 写入语义：系统里没有「最后写入者」

- **现象**：整份 JSON 覆盖，四条路径（PUT / AI apply / 信息库导入 / 版本恢复）各写各的，只有恢复带秒级乐观锁；前端保存响应会整体替换本地对象；卸载只取消防抖不落库。
- **本质**：写入协议只有「内容」，没有「基于哪一版」。两条正确实现之间没有约定，谁后到谁赢，而且赢家不告诉输家。
- **只修 bug 会怎样**：修完 RF-001（响应不回写）后，RF-005（失焦 PUT 与 apply 竞争）仍然在；再修 RF-005 后，RF-006（旧提案覆盖）仍然在——因为「覆盖」这个动作本身没有版本前提。**必须一次把版本号引进写入协议**，否则每条都是独立补丁。

### M2 回合身份：最小记账单位选错了

- **现象**：`ai_chat_messages`、`ai_tasks`、`ai_traces`、工具回调四者互不指认；一次对话回合的三次 commit 分属三个请求生命周期。
- **本质**：系统把「任务行」当成回合，但一个回合可能产生 0～N 个任务行（多模型、摘要压缩、回调丢失）。**账本能平，回合永远对不上**，于是任何异常态都只能靠人查库。
- **只修 bug 会怎样**：给 usage 加幂等键能止住重复扣费，但「这轮到底交没交付、该不该退」依然答不出来——RF-040 的三种残缺态、RF-018 的口径错误都还在。

### M3 生命周期：所有中间态都没有主人

- **现象**：`pending` 任务无 TTL、预扣无租约、`pending` 提案无 supersede、上传无状态列、`lifespan` 只打日志不跑任何清扫。
- **本质**：系统只有「开始」和「用户操作触发的结束」，没有「时间触发的结束」。进程一停，中间态永久停留，而运营只能手工补。
- **只修 bug 会怎样**：给预扣加 `expires_at` 但不加清扫器，等于加了一个没人读的列；加了清扫器但退款仍走 ORM 读改写（RF-015），并发退款照样丢更新。

### M4 参数与契约：同一个值在多处各写一遍

- **现象**：标题长度 schema 120 / service `[:120]` / 列宽 `String(100)`（已复核，`duplicate_resume` 必然 500）；`pdf_base_url` 一个值同时喂 Chromium 与浏览器（`preview_service` 的字体与头像都走 `render_url`，而 `avatar_service.browser_url()` 就在旁边没人用）；超时三层语义不同（代理 180s 空闲 / Node 300s 总时长 / 代码默认 60s）；连接池不配用默认 5+10；附件上限与 Node body 上限错配。
- **本质**：跨层契约没有唯一来源，也没有启动时的自洽断言。**每一层单独看都对，交界的两个数不一致**。
- **只修 bug 会怎样**：这三处改动都是一到三行，修完立刻消失——正因为便宜，它们应该进第一周，而不是排在「性能周」。

### M5 可证明性：正确性只能靠人读代码

- **现象**：看板成功率读 `status`（一个字段两种语义）；用户记录里出现内部任务；热查询缺组合索引；缺池等待、线程池占用、pending 年龄、漏计费计数；前端保存时序零覆盖、无 E2E。
- **本质**：系统没有「不看代码就能回答『这轮谁扣了多少钱、交没交付』」的能力。于是每次事故都是一次人工取证。
- **只修 bug 会怎样**：修完 M1/M2/M3 后的效果无法验收——没有对账视图，你无法证明「现在不丢了」。

### M6 信任模型：三类主体用同一把尺子

- **现象**：用户 JWT 是唯一有签名、有有效期的凭据；服务间是**无 audience、无过期、无回合绑定**的静态令牌 + 明文 `user_id`；访客侧自定义分享 token 最短 3 位、脱敏只覆盖 `basics`。
- **本质**：`user → service → 回调` 这条链上，身份退化成「请求体里的一个整数」。同时生产启动只校验 BYOK 密钥，不校验 JWT 强度。
- **只修 bug 会怎样**：814 端口收窄、令牌加 audience 都是局部；只要没有「三类主体各自的凭据强度、作用域、有效期」这一张表，下一个新端点还会照抄旧的宽松写法。

---

## 3. 方案总结构：四层，S0 到 S3

| 层 | 目标 | 工期 | 出口标准 |
| --- | --- | --- | --- |
| **S0 定格事实** | 把 14 条悬空结论变成实证 | 0.5 天 | 报告等级表更新；P0 名单确认或调整 |
| **S1 止血** | 消掉「确定性、低成本、用户可见」的故障 | 1 周 | §5 表格全绿，每项带一条回归用例 |
| **S2 机制** | 建立 M1～M6，让一整片缺陷失去存在条件 | 2～3 周（可并行） | §6 每条机制的「不变量」有自动化断言 |
| **S3 防复发** | 审查/护栏/运营三件套 | 第 4 周起持续 | 下一轮 review 能直接接着审，不需要重新对齐 |

关键路径：**S0 → S1（并行可开工）→ M2 → M3 → M5**。M1 与 M4 可独立并行；M6 与 S1 同批最省事。

---

## 4. S0：环境核实与待验证项定格（半天）

### 4.1 四条部署事实（决定 P0 是否成立）

| 编号 | 要核实什么 | 怎么核 | 结论分支 |
| --- | --- | --- | --- |
| RF-003 | `APP_ENV`、`JWT_SECRET_KEY` 是否仍是 `.env.example` 原值、是否配了独立 BYOK 密钥 | 读生产 `.env`（不看值，只看是否等于默认串长度/哈希指纹）＋ 用默认密钥签 `sub=1` 调 `/auth/me` | 命中即 P0 立即修；未命中降 P1 但仍必修 |
| RF-008 | 8300 是否从宿主机可达；`AI_SERVICE_TOKEN` 是否轮换过 | `docker compose ps` + 从另一台机器 `curl :8300/health` | 可达即 P1；仅容器网内则降 P2 |
| RF-052 | `PDF_BASE_URL` 实际值；用户从哪个域名访问 | 读 compose/`.env`；浏览器打开预览看 Network 主机名 | 非 127.0.0.1 或非本机访问即必修 |
| RF-011 | `PI_PROVIDER` 是否为空（默认 faux） | `docker exec` 打印环境变量 | 生产用 faux → 立刻按最高级处理（跨用户串扰） |

### 4.2 十条待验证（每条 ≤15 分钟的复现）

| 编号 | 复现动作 | 判定 |
| --- | --- | --- |
| RF-005 | 拦截 PUT 延迟 800ms，点「应用」 | 库内两处修改丢一处 → P1 确认 |
| RF-009 | 发起生成后立即断开客户端，等 15 分钟 | 任务仍 pending + 点数未退 → P1 确认 |
| RF-053 | 20 并发 `/preview-pdf` 同时打 `/api/health` | health 排队 >1s → 确认「一压全站挂」 |
| RF-040 | `result` 到达前 kill 后端 | 三表出现孤儿/幽灵 → 确认 |
| RF-055 | 同 IP 用 100 个邮箱试错密码，测登录 P95 | 无 429 且 P95 抬升 → 确认 |
| RF-022 | DevTools Performance 录 10s，数 `updatePageCount` | ~160 次 → 确认循环 |
| RF-003 补充 | 用默认密钥签 token | 200 即完全账户接管 |
| RF-036 | `duplicate` 传 110 字符标题 | 500 → 确认（已静态复核，仅需实证） |
| RF-012 | 用指向 127.0.0.1 的域名做 BYOK base_url 并发对话 | 连通即 SSRF 成立 |
| RF-020 | 带图片要求写入编造离职时间 | 未被拦 → 守卫失效 |

**产出**：一张「静态 → 实证」对照表，回填报告 §3 的状态列与 §10.7 的必修清单。若 RF-003/011 命中，P0 从 3 条升到 4～5 条，S1 顺序要改。

---

## 5. S1 止血清单（1 周，全部是小改动）

原则：**只做「后果确定 + 改动局部 + 不改协议」的项**。任何需要引入新字段/新表的都推到 S2。

| # | 事项 | 位置 | 规模 | 回归用例 |
| --- | --- | --- | --- | --- |
| 1 | 生产无条件拒绝默认/短于 32 字节 JWT 密钥，与 BYOK 解耦；`.env.example` 改必填 + 生成命令 | `core/config.py`、`main.py`、`core/crypto.py` | ~30 行 | 启动即断言拒绝 |
| 2 | 保存响应只并元数据（`update_time/title/template_id`），正文保留本地 | `stores/resume.ts:231-242` | ~10 行 | 延迟响应 + 在途输入 |
| 3 | 卸载/切路由统一 `flushPendingSave()`；补 `pagehide` + `sendBeacon` | `ResumeEditor.vue`、`router/index.ts` | ~30 行 | 输入后 200ms 跳转 |
| 4 | 「应用」默认提交「当前数据 + 全选 diff」合并结果 | `ResumeEditor.vue:1025`、`useResumeChatStream.ts` | 复用 `buildSelectedResumeData` | 提案后改手机号再应用 |
| 5 | 渲染地址按消费者分流：预览/分享走 `browser_url` + 相对 `@font-face` | `preview_service.py:124,196`、`avatar_service.py` | ~15 行 | 断言 `/preview-html` 不含 `127.0.0.1` |
| 6 | 标题长度收敛到 100（schema / service / 前端 `maxlength` 三处） | `schemas/resume.py:191`、`resume_service.py:155`、编辑器输入 | ~6 行 | `duplicate` 传 110 字符应 200 |
| 7 | 上传先查 `Content-Length`/`file.size` 再读，分块累计判上限 | `storage_service.py:161-258` | ~40 行 | 直连 8000 传 500MB 应 413 |
| 8 | 公告已读改 `INSERT ... ON DUPLICATE KEY`/捕获 `IntegrityError` | `api/announcements.py` | ~15 行 | 双标签页同开 |
| 9 | 脱敏扩到 `summary` + `custom_sections` 的联系方式（正则掩码） | `resume_share_service.py:82-99` | ~40 行 | summary 含手机号 → 分享页无该号码 |
| 10 | 自定义分享 token 下限 3 → 8 位，公开 GET 加 per-IP 限频 | `resume_share_service.py:25`、`api/shares.py` | ~10 行 | 3 位 token 应校验失败 |
| 11 | Node 两个 async handler 包 try/catch + `unhandledRejection` + 错误中间件 | `server.ts:607,819` | ~50 行 | 注入必抛配置，进程不退出 |
| 12 | apply/reject 改条件 UPDATE（`WHERE id=? AND action_status='pending'`），rowcount=0 → 409 | `ai_chat_service.py:1626` | ~20 行 | 并发 apply/reject |
| 13 | 退款走 `_apply_balance_delta`，禁止调用方赋值 `flow_points` | `ai_task_service.py:79-110` | ~15 行 | 并发两笔退款总额相符 |
| 14 | 登录加 per-IP 维度（保留邮箱维度） | `api/auth.py:124-134`、`core/throttle.py` | ~20 行 | 100 邮箱/1 IP 应 429 |

> 第 5、6 两条投入产出比最高（合计 ~20 行，消掉两个用户可见故障），即使 S0 结论有变也应保留。

**进度**：2026-11-21，已落地四批（后端 549 → 562 用例，前端 248 用例，ai-service 172 → 175 用例，每条都做过「改回去立刻变红」的负向验证）。

- **第 1 批**：#1 / #5 / #6（RF-003、RF-052、RF-036）。三处都按「单一来源」落地：JWT 校验独立于 BYOK 且生产拒绝启动；`RESUME_TITLE_MAX` 成为列宽 / 校验 / 截断 / 输入的共同来源；渲染地址由 `absolute_assets` 显式声明消费者。
- **第 2 批**：#2 / #3 / #4（RF-001、RF-004、RF-002）。要点：保存响应只回写元数据、`performSave` 用内容指纹判断在途编辑；路由守卫 + 卸载 flush + `pagehide` keepalive；「应用」默认提交合并结果（全选结果必须等于提案本身，作为不变量断言）。
- **第 3 批**：#7 / #8 / #9 / #10 / #14（RF-054、RF-058、RF-056、RF-059、RF-055）。要点：上传改为分块读取、超限即在读循环内拒绝（上限仍返回 400，与既有上传错误一致，不改成 413）；公告已读改为 savepoint 插入 + 捕获 `IntegrityError`，计数改条件自增；脱敏在清空 `basics` 后对整份数据递归掩码；token 下限 8 位且 `/shares/{token}` 按 IP 限速（`hit()` fail-open）；登录补 per-IP 失败维度。
- **第 4 批**：#11 / #13（RF-010、RF-015）+#7 之外的计费止血（RF-007 回合幂等、RF-028 发放幂等）。要点：`ai_tasks` 加 `turn_id` + `turn_slot`，唯一约束落在两者组合上（一个回合可能有多个模型），命中即按任务行还原首次响应；退款与全员发放/撤回三处统一走 `add_point_transaction`，全仓不再有 `flow_points` 赋值；`grant-all` 由 `request_id` 派生批次号并跳过已发放用户（半途中断可续跑）；Node 错误出口抽成 `httpErrors.ts`，未 flush 返回 500 JSON、已 flush 补 `error` 事件。迁移 `0044_ai_task_turn_id` 已在 MySQL 实测 upgrade/downgrade。
- **未开工**：#12（apply/reject 条件 UPDATE，属 S2 的状态机收敛，和 RF-006 一起做更合适）。

---

## 6. S2：六个机制的具体设计

每条给出**不变量**（可写成断言/测试）、**落地步骤**、**兼容与灰度**。原则是「先加字段后改行为」，不做不可回滚的大改。

**进度（2026-11-22）**：M1–M6 全部落地。用例：后端 562 → 732，ai-service 175 → 188，前端 249 → 251。

### 6.1 M1 写入语义：一个写函数 + 一个版本号

**不变量**：任一时刻，简历的每次写入都声明「我看到的是第 N 版」；不匹配则拒绝并告诉调用方最新版。

报告建议用 `expected_update_time`，但 `update_time` 是秒级（RF-057 已指出同秒内失效），且与「回读比对」混用。**建议改用整数 `revision`**：

1. 迁移：`resumes`/`resume versions` 增 `revision INT NOT NULL DEFAULT 0`；已有数据置 0。
2. 后端：`persist_resume_data(db, resume, data, *, source, expect_revision)` —— normalize → 校验 → 写前快照（仅 `source != manual` 或内容有变化）→ `UPDATE resumes SET ..., revision = revision + 1 WHERE id=? AND revision=?` → rowcount=0 返回 409（带最新 `revision` 与摘要）→ 回读比对。
3. 四条路径只传 `source`：`manual` / `ai_apply` / `import` / `restore`。**恢复路径的秒级锁一并替换**。
4. 前端：保存请求带本地 `revision`；409 时提示「已在别处更新」并给「查看差异 / 覆盖」两个出口，而不是静默覆盖；保存成功后只更新 `revision` 与元数据（与 S1-2 合并）。
5. 兼容：`expect_revision` 可缺省（缺省＝不校验）；灰度期先只让编辑器带，其余三路径第二阶段接。

**验收**：RF-001/002/005/006/013/057 六条对应的场景全部通过；`resume_versions` 加 `(resume_id, content_digest)` 唯一索引 + 捕获 `IntegrityError`（RF-057）。

**落地（2026-11-21）**：

- 迁移 `0045_resume_revision`：`resumes.revision` 与 `resume_versions.revision`（INT NOT NULL DEFAULT 0，带 server_default）；本地 MySQL upgrade → downgrade → upgrade 已验。
- `persist_resume_data(db, resume, data, *, source, expect_revision, user_id, values)` 成为唯一写入口：条件 `UPDATE ... WHERE revision = ?`、`revision = revision + 1`、`update_time/update_by` 由它统一维护；rowcount=0 抛 409 并带 `latest_revision`；写完回读比对（JSON 截断只有回读看得见）。`data=None` 表示只改元数据。
- `stage_version()` 取代「写快照 + commit」：AI 采纳与版本恢复把快照暂存在同一事务里，写入撞 409 时一起回滚。`create_version` 仍负责手动存档的提交与清理。
- 四条路径只传 `source`：`update_resume`（manual / import）、`_persist_resume_change`（ai）、`restore_version`（restore）。恢复的秒级 `expected_update_time` 换成 `expected_revision`，老字段保留作兼容。
- `AppException(..., data=)` + `error_response(..., data=)`：409 终于能把「服务端现在是第几版」告诉前端。
- 前端：`ApiError`（保留 status/code/data）替换裸 `Error`；保存请求带 `expect_revision`；409 时**暂停自动保存**并显示冲突条，给「载入最新版 / 保留我的版本」两个出口；关页 keepalive 与「覆盖」显式不带版本号；`beforeunload` 在有未解决冲突时拦一下。
- 验收差异（如实记录）：**RF-006 只收窄未关闭**——写写竞争不再丢数据，但「旧提案内容本身过期」仍需 supersede + 条件 UPDATE，并入 M3；**RF-057 的重复快照唯一索引未做**，本轮只关掉了恢复路径的 TOCTOU。
- 用例：新增 `backend/tests/test_resume_revision.py`（真 SQLite，12 条）；后端 562 → 574，前端 248 → 249。负向三处（去掉 WHERE 版本判断 / 409 不带版本号 / 恢复忽略 revision）与本条冲突探针均验过「改回去立刻红」。

### 6.2 M2 回合身份：`turn_id` 贯通四表

**不变量**：凡是「为这一轮对话」产生的事实，都带同一个 `turn_id`；同一 `turn_id` 的 usage 只结算一次。

1. `turn_id` 取**用户消息主键**（commit ① 已落库、天然唯一有序，且与前端「重发」语义一致）。
2. 落点：`ai_chat_messages.turn_id`（自指可省，用 id）→ `ai_tasks.turn_id`（**唯一索引**）→ `ai_traces.turn_id` → 工具回调 body + `/service/usage` body。
3. 幂等：usage 命中 `turn_id` 直接 200 返回已有任务；`trace` 已用 upsert，照抄同一模式。
4. 顺带补 `session_id` 到工具回调（RF-014），缺失时**报错而不是回落最近会话**。
5. `update_resume` 单槽位改按 section 合并（RF-019），result 回传合并快照。

**验收**：kill -9 后重启，用一条 SQL join 出「每回合一行：有没有助手消息 / 有没有任务 / 扣了多少」；同 `turn_id` 连发两次 usage 只扣一次。

**落地（2026-11-21，分两批）**：

- 第 4 批（S1）：`ai_tasks.turn_id` + `turn_slot`、`(turn_id, turn_slot)` 唯一索引；`/service/usage` 命中已结算回合直接重放；轨迹与 usage 上报都带 `turn_id`。`turn_id` 取被回复的用户消息 id。
- 第 5 批（M2 收尾）：**`session_id` 贯通工具回调**——代理层在派发前就用 `get_or_create_chat_session` 定下会话，随 `/v1/chat` 传给 Node，Node 再写进 `/service/tool/update-resume` 的请求体；后端缺 `session_id` 时**返回 400 而不是回落「最近活跃会话」**（RF-014）。历史与数值守卫现在只看这一轮所属会话。
- **一次回合内多次 `update_resume` 合并**：`TurnState.pendingChange` 仍是一个槽位，但每次调用都把已有快照作为 `base_resume_data` 回传，后端从这份快照起算而不是从库里的旧数据（RF-019）。摘要按顺序累积去重，模型据此汇报，不会只报最后一次。
- 未做（归 M5）：验收里那条「每回合一行」的对账 SQL 属于可证明性，M5 统一提供，本轮只保证 `turn_id` 已经把三张表串起来。
- 用例：后端新增 3 条（缺 `session_id` 400、历史按会话取、`base_resume_data` 叠加），ai-service 新增 2 条（缺 session 拒绝回调、二次调用带 base 且摘要累积）；后端 574 → 577，ai-service 175 → 177。

### 6.3 M3 生命周期：租约 + 清扫器 + 幂等键

**不变量**：任何非终态都有 `expires_at`，且有进程负责在到期后收尾；清扫本身幂等。

1. `ai_tasks` 增 `expires_at`、拆 `billing_status`（`unbilled/billed/failed`）；`status` 只表示执行结果（RF-017）。
2. 预扣改「冻结 + 结算」：扣点即记 `pending` 流水；到期未结算则退款（走 `_apply_balance_delta`）。
3. **清扫器**（单 worker 下进程内 `asyncio` 任务即可，不必引调度框架）每 5 min 跑四件事：pending 任务超 15 min → failed+退款；pending 提案超 24 h → superseded；孤儿上传对象（>24h 无索引）；孤儿回合（用户消息 >10 min 无助手消息且无任务）。
4. 幂等键：管理员全员发放（`request_id` 唯一）、创建/复制简历、提交反馈——统一一张 `idempotency_keys(scope, key, user_id, response_hash)` 或各自唯一约束，优先后者（改动小）。
5. 清扫器必须可被 SIGTERM 优雅停止（否则重演 RF-045）；每次清理都要计数上报（供 M5 用）。

**验收**：断连 15 min 后任务 failed + 点数回原值；清扫器重启后不重复退款。

**落地（2026-11-21）**：

- 迁移 `0046_lifecycle_leases`：`ai_tasks.billing_status`（`unbilled/billed/refunded/failed/none`）+ `expires_at`，`resumes.request_id` / `user_feedbacks.request_id` 与两个唯一约束。历史行按 `status` 回填 `billing_status`，pending 行补 `create_time + 15 min` 的租约（否则迁移后它们永远等不到收尾）。本地 MySQL upgrade → downgrade → upgrade 已验。
- **两个字段正交**：`status` 只表示执行结果，`billing_status` 只表示钱。交付与结算分两个事务落地——结算抛错只写 `billing_status=failed`，`status` 保持 `success`（RF-017）。用户自带模型（`settle=False`）显式写 `none`，否则会被当成「待补结算」被清扫器补扣。
- **清扫器** `services/maintenance_service.py`：进程内 `asyncio` 任务，`lifespan` 启停，每 5 分钟六件事——(1) 租约过期的 pending 任务 → failed + 退款；(2) 已交付未结算的 success 任务 → 补结算；(3) 过期 pending 提案 → superseded；(4) 失联 applying → stale；(5) 孤儿回合补 failed 占位任务；(6) `ai-chat/` 孤儿上传对象。每个子任务独立 try/except 并计数，失败项记 `-1`，一类数据畸形不会让整轮停摆。`SIGTERM` 时 `await stop()` 等当前一轮跑完。
- **幂等靠条件 UPDATE 而不是锁**：`UPDATE ... WHERE status='pending' AND expires_at < now` 的 rowcount 决定谁负责退款，所以重复清扫、多实例清扫都只退一次；Redis 锁只是「少做无用功」，拿不到也照常跑（幂等）。
- **孤儿上传默认只统计不删**：头像对象即便索引行已被用户删除，也可能仍被分享简历引用（`avatar` 删除只清索引），自动删对象会打断外部访客看到的页面。只有 `ai-chat/` 前缀按开关删除，默认 `MAINTENANCE_ORPHAN_DELETE_ENABLED=false`。
- **幂等键**：不给「创建/复制简历、提交反馈」加新表，各自在业务表上加 `request_id` + `(user_id, request_id)` 唯一约束（MySQL 唯一索引对 NULL 不敏感，历史行不受影响）。先查一次做快路径，插入撞约束则回滚回读赢家——后者才是真并发时唯一生效的那条。前端 `utils/requestId.ts` 生成，**一次用户动作一个键、成功才作废**，失败重试复用同一个。
- 未做：`billing_status` 只落在任务行，没有单独的「冻结流水」；清算（把失败任务的钱真正回到账本）仍复用 `add_point_transaction`，**没有引入第二套余额**——两段式冻结会让账本多一层状态，收益不抵复杂度。

**验收**：断连 15 min 后任务 failed + 点数回原值；清扫器重启后不重复退款。**实测（真 MySQL + 真 HTTP）**：预扣 7.50 → 管理端手动清扫 → 任务 `failed/refunded`、余额回到 96412.75、第二轮清扫 `tasks_expired=0`。

### 6.4 M4 参数单一来源 + 启动自检

**不变量**：一个参数只有一个来源；同一语义的多个参数必须满足声明的关系，否则拒绝启动。

1. 建 `core/contracts.py`：`TITLE_MAX=100`、`UPLOAD_MAX`、`TURN_TIMEOUT`（Node ≤ backend read ≤ nginx）、`RENDER_BASE`（pdf=internal / browser=relative）、`POOL_SIZE/OVERFLOW/TIMEOUT`。模型列宽、schema `max_length`、service 切片、前端常量全部引用它。
2. `pool_size=10, max_overflow=20, pool_timeout=5` 显式配置（RF-053）；`/preview-html`、`/preview-pdf` 渲染前先结束只读事务。
3. 启动自检（`lifespan` 内）：production 下断言 JWT 强、`PDF_BASE_URL` 非回环、超时单调、池参数显式、`PI_PROVIDER != faux`。任一不满足**拒绝启动并打印人话**。
4. 契约测试：一个 pytest 断言「模型列宽 == schema 上限 == 前端常量」（前端常量可由构建脚本生成，或直接读 TS 源文件比对）。
5. 顺带消掉文档漂移（00 §9.7 的 G1–G12）与两处重复清单（模板元数据三处、技能清单一式两份）。

**验收**：改标题上限只改一处；把 `TURN_TIMEOUT` 调成小于 read 超时，服务拒绝启动。

**落地（2026-11-22）**：

- **`core/contracts.py` 成为唯一来源**：`RESUME_TITLE_MAX`、`REQUEST_ID_MAX`、`UPLOAD_MAX_BYTES`、`CHAT_CONTENT_MAX`、`CHAT_SESSION_TITLE_MAX`、`CHAT_ATTACHMENT_MAX`、`CHAT_ATTACHMENT_TOTAL_MAX_BYTES`、`NODE_BODY_MAX_BYTES`、`NGINX_CLIENT_BODY_MAX_BYTES`、`AI_TURN_TIMEOUT_MS` / `AI_PROXY_READ_TIMEOUT_MS` / `NGINX_PROXY_READ_TIMEOUT_MS`、`DB_POOL_SIZE` / `DB_MAX_OVERFLOW` / `DB_POOL_TIMEOUT_SECONDS`。模型列宽、Pydantic 上限、storage 上限、`config.py` 默认值全部引用它；模块本身零依赖，避免与 settings 循环导入。
- **两条纯函数承载关系**：`timeout_chain_problems()`（相邻两档至少留 30s）与 `chat_body_budget_problems()`（附图 base64 后装得进 Node，且 Node 严于 Nginx）。它们既被启动自检调用，也被契约测试直接调用，规则只写一遍。
- **启动自检 `core/startup_checks.py`**：静态档（超时链、请求体预算、池超时、`PDF_BASE_URL` 是否绝对地址）在 production 下失败即拒绝启动、一次列全全部问题；跨服务档通过 `GET /v1/contracts` 读 Node 自报的 `turn_timeout_ms` / `body_limit_bytes` / `provider`，连不上只 warning（启动顺序问题），**连上但不自洽一律拒绝**（含生产仍跑 faux）。
- **契约测试直接读源码**：`tests/test_cross_layer_contracts.py` 解析 `types/resume.ts`、`types/chat.ts`、`AiComposer.vue`、`ai-service/src/config.ts`、`server.ts`、`frontend/nginx.conf`、`docker-compose.yml`，比对数值；另加两条清单比对（模板元数据三处、技能清单一式两份）。
- **RF-053 一并收掉**：池参数显式化 + 四个渲染入口渲染前 `db.rollback()` 归还连接（`preview_pdf` / `preview_html` / `preview_html_by_data` / `export_pdf`）。
- **修正计划里两条与事实不符的前提**：(1) 「`PDF_BASE_URL` 非回环」不能作为生产硬断言——Chromium 与后端同容器时回环地址是**正确**配置（compose 默认），改成「必须是绝对 http(s) 地址」+ 回环仅告警；(2) 「`/preview-html` 传未保存草稿保证实时一致」是旧描述，实际普通预览走 GET（已保存数据），只有审阅模式走 POST。
- **实测**：正常配置启动 → 日志出现「启动自检通过」（且未跳过跨服务核对，说明真的连上了 ai-service）；`APP_ENV=production` + `AI_PROXY_READ_TIMEOUT_MS=100000` → 进程拒绝启动并点名「回合会被代理先掐断」。契约测试**当场抓到两处真实漂移**：`modern` 前端叫「现代侧栏」后端叫「现代双栏」、`tech` 叫「技术蓝线」对「技术岗位」，已按后端注册表统一（前端兜底名 + `StyleConfigPanel` 下拉共两处）。
- **未做**：(1) 00 §9.7 的 **G9**（`ai/ai-call-flows.md` 等文里的代码行号）——那两份文档已声明「以符号名为准」，逐行更新只会再漂一次，不修；(2) 常量没有代码生成（前端仍是手写一份，靠测试锁住），因为为一个数字引入构建期代码生成，收益不抵维护成本；(3) 连接池饱和度指标属 M5。

### 6.5 M5 可证明性：六指标 + 一张对账视图

**不变量**：任何一次故障后，不看代码也能回答「这轮谁扣了多少钱、交没交付、现在有没有卡住」。

1. 指标最小集：连接池 checkout 等待、anyio 线程占用、pending 任务年龄分布、回合成功率（按交付口径）、**漏计费计数**（有助手消息无任务）、PDF 队列深度。
2. 对账视图（一条 SQL + 一个后台页）：回合级 join、`SUM(points_delta)` 与余额比对、`status=success AND billing_status!=billed` 清单。
3. 看板口径修正：按 `status` 统计交付率，按 `turn_id` 去重统计回合数（RF-017/018/061）。
4. `my_ai_records` 默认排除 `chat_summary`；补 `(user_id, created_at)` 组合索引。
5. 关键写入的日志带 `revision`/`turn_id`，使事后复盘只需 grep 两个键。

**验收**：制造一次「断连 + 回调丢失」，仅凭对账页定位到该回合与差额。

**落地（2026-11-22）**：

- **指标 `core/metrics.py`（零依赖，不碰 DB）**：`LatencyTrack` 是固定 512 槽的滚动采样窗口（`P50/P95/P99/max/count`，重启清零、不是全生命周期累计，`count` 才是累计次数）；`MeasuredQueuePool` 继承 `QueuePool` 重写 `_do_get`，在**业务无关**的一层记录「拿连接等了多久」——不改任何调用点；`thread_snapshot()` 读 anyio 默认 limiter 的 `total/borrowed`（同步任务占线程池的饱和度）；`pdf_snapshot()` 由 `pdf_render_waiting/started/finished` 三钩子维护「在跑 / 在等」，直接回答「PDF 队列有多深」。连接池那一层用子类而不是 monkey patch，是因为 patch 会在任何一次 import 顺序变化后静默失效。
- **对账 `services/reconciliation_service.py`（全部是 SQL 聚合，不把行拉进 Python）**：`TURN_LEDGER_SQL` 一条 join 出每回合一行的 `user_msg / assistant_msg / task / points / diff`，并把状态归一成五种 `state`——`ok`（交付且账平）、`unbilled`（交付未结算）、`missing_reply`（有任务无回复）、`missing_task`（有回复无任务）、`billing_failed`；另有 `balance_mismatches`（`SUM(points_delta)` vs `users.flow_points`）、`delivered_unbilled`、`ghost_tasks`、`pending_age`（四档年龄 + 无租约 + 逾期）、`turn_success`（`turn_id` 去重口径）。`reconcile()` 把它们拼成一个结论对象，顶层 `healthy` + `unhealthy` 十个计数器——**前端不需要懂业务就能判断要不要标红**。
- **两个接口刻意不合并**（`api/admin/observability.py`）：`GET /admin/observability/metrics` 秒回、可频繁拉；`GET /admin/observability/reconciliation` 要扫表、只在排查时拉。合成一个的结果是前者被后者拖慢，然后没人再点前者。
- **看板口径（RF-018）**：新增回合口径 `count(distinct turn_id)`，与行口径并列；`rates.ai_turn_success` 与 `turns.tasks_per_turn`（一回合几个模型）直接在质量页显示。`my_ai_records` 与它的计数**共用同一个过滤条件**，把 `chat_summary` 这类内部任务挡在用户视野外（`AI_INTERNAL_TASK_TYPES` 单点定义，两处分开写是这类问题最经典的复发方式）。
- **索引（RF-061）**：迁移 `0047_reconcile_indexes` 给三张热表补 `(user_id, create_time)`；本地 MySQL upgrade → downgrade → upgrade 已验。
- **日志可取证**：`persist_resume_data` 每次成功写入留 `resume_id/revision/source/user_id`；`finish_ai_task` 每次收尾留 `task_id/turn_id/type/status/billing/points`。一次故障回合的完整还原 = `grep turn_id=<id>` 再看 `grep revision=`，不必现场拼三张表。
- **实测（真 MySQL + 真后台接口）**：造「回复已落库、usage 回调丢失」+「差额 2.0 点」+「余额被别处改过」三种残缺，一条对账查询同时命中：回合 A `state=unbilled` 差额 2.0、回合 B `state=missing_reply`、已交付未结算清单命中任务、余额不符清单命中用户（余额 92.0 / 流水 97.0），并给出 `healthy=false` 与逐项计数。
- **顺带发现一处真实数据漂移**：对账页第一次跑就报出本地库有个用户余额比流水少 7.50——追下去是 M3 探针清理数据时**只删了预扣那侧的流水、留下了配对的退款流水**，余额本身是对的。这类「账本单边残留」正是这个页面的存在意义；已清掉孤儿流水，本地库恢复零不符。
- **未做**：(1) 看板主体仍是 Python 统计，只补了索引，没有改写成纯 SQL 聚合——它要多口径同比与分桶，改写后更难核对，而索引到位后扫描量已不是瓶颈；(2) 指标是**进程内**的，多副本部署时需要外部聚合（没有 Prometheus 导出），`P95` 是最近 512 次样本而非全量；(3) 没有统一 `request_id`/trace id，只加了 `turn_id` 与 `revision` 两个键——够复盘一次回合，不够追一条跨服务的请求链；(4) `/api/health` 端点仍不存在（S3 的「20 并发导出时 /api/health p95」场景要先补它）。

### 6.6 M6 信任模型：三类主体一张表

| 主体 | 凭据 | 作用域 | 有效期 | 吊销 |
| --- | --- | --- | --- | --- |
| 用户 | JWT + `token_version` claim | 自身资源 | 24h | 改密自增 `token_version` |
| 服务（B→N→B） | 短期签名（audience + 回合绑定）或静态令牌 + 定期轮换 | 单回合 + 单 user/resume | ≤ 回合时长 | 轮换即失效 |
| 访客 | 分享 token（≥8 位） | 单份公开简历 | 用户设定 | 停用/过期 |

配套：8300 改 `127.0.0.1:8300`；`/service/usage` 校验 `turn_id` 归属；BYOK 每轮解析并拒绝私网 + 禁重定向（RF-012）；图片改短签名 URL（RF-025）；容器非 root（RF-037）。

**验收**：用服务令牌给他人记账应 403；改密后旧 token 立即 401。

**落地（2026-11-22）**：

- **用户令牌可吊销（RF-027）**：`users.token_version` + JWT `tv` claim；`get_current_user` / `user_from_token` 校验 claim 与库里版本一致。改密、重置密码都自增版本号并顺手签发新 token 给当前会话，其他设备的旧 token 立即 401。**无 `tv` claim 的历史 token 放行**（平滑升级），避免升级瞬间把全部在线用户踢下线。迁移 `0048_token_version`。
- **服务回合票据（RF-008）**：`core/service_ticket.py` 用 JWT 密钥派生 HMAC 签短期票据（user/resume/turn/exp，TTL 覆盖整个回合）。后端调 Node 时随请求体下发 `turn_ticket`，Node 只当搬运工原样回送；`require_turn_ticket` 双证——静态服务令牌证明「你是 AI 服务」，票据证明「你正在替这个用户办这一回合」。作用域不符返回 **403**（令牌票据都有效，是拿 A 的票据办 B 的事），缺票据/坏票据 401。所有 `/service/*` 回调（工具 6 个 + usage + trace）都过这道关。8300 改绑 `127.0.0.1:8300` 并有契约测试守住。
- **BYOK SSRF（RF-012）**：`ai-service/src/ssrf.ts` 三层——每轮 `registerByokModel` 解析目标域名并拒绝私网/回环（挡 DNS rebinding 的窗口）；`dns-lookup.ts` 对登记过的主机在**拨号那次解析**上再过滤私网地址（用 `BlockedTargetError` 阻断系统解析回落）；`installOutboundFetchGuard` 对 BYOK 主机禁重定向（公网域名 302 到内网不经过域名解析）。平台自己的 provider 不受限——自托管模型跑在内网是正常部署。
- **图片短签名 URL（RF-025）**：`core/signed_url.py` 用 JWT 密钥派生 HMAC 签 `sig`+`exp`（默认 6h，`FILE_URL_TTL_SECONDS` 收进 contracts）。上传响应、历史消息读取都重新签；`<img>` 不再需要把 24h 登录 token 拼进 URL。文件接口**不再接受 query token**，只认短签名或 Bearer 头。生产环境 `STORAGE_PUBLIC_URL_MODE=public` 启动自检拒绝（公开地址会同时绕过登录与签名）。
- **容器非 root（RF-037）**：backend 镜像 `useradd uid 10001 app` + `USER app`（只把 `storage/` 交给它可写）；ai-service 用 node 镜像自带 uid 1000 的 `node` 用户。实测容器内 `uid=10001(app)` / `uid=1000(node)`，`storage writable: yes`。契约测试断言两个 Dockerfile 都有非 root `USER`。

**未做**：访客分享 token 仍是旧机制（没并进统一的票据体系）；BYOK 主机登记靠进程内状态，多副本各自解析（无共享缓存）；没有独立的服务令牌轮换流程（仍单一静态令牌，靠 8300 回环 + 票据补强）。

---

## 7. S3 可证明：把验收写成场景，而不是写成清单

现有测试底子不差（后端 524 用例、前端 26 spec、30 格 UI 审计、`vue-tsc`），缺的是**时序与并发**。建议按「改坏会红」的原则补五组场景，每组 2～4 条：

| 组 | 场景 | 自动化手段 |
| --- | --- | --- |
| 保存时序 | 延迟 PUT + 在途输入 / 200ms 内切路由 / 关页 `pagehide` / 双标签页 | Playwright `page.route` 延迟响应 |
| 回合一致性 | 断连后清扫 / 重复 usage / 旧提案 apply / 并发 apply+reject | pytest + 线程并发 + 直连 DB 断言 |
| 资金 | 并发退款 / 透支记账 / 全员发放重放 | pytest 并发 + `SUM(delta)==余额` 断言 |
| 渲染 | `/preview-html` 不含 `127.0.0.1` / 字体与头像可加载 / 110 字符标题复制 200 | pytest 断言响应体 + 一条 Playwright |
| 容量 | 20 并发导出时 `/api/health` p95 < 200ms | 脚本并发 + 断言 |

纪律：**每条 S1/S2 改动必须带一个这样的用例**，且写完要做一次负向验证（把修复改回去，确认用例变红）——这一条已在项目里形成了习惯，继续保持。

---

## 8. S3 防复发：让下一轮审查更便宜

### 8.1 审查流水线

- 固定 5 个 gate（架构 / 后端 / AI / 前端 / 跨服务）+ 1 次交叉验证，**任何 gate 缺失即视为审查未完成**（本轮教训）。
- **单一编号**：只保留 `RF-xxx`；专项报告里的 `B/C/F/L` 编号在归档时全部映射，不再新增体系。
- **living doc**：以 `code-review-report.md` 为唯一入口，每轮只更新「新增 / 关闭 / 降级」三列 diff；专项报告完成映射后移入 `docs/code-review/archive/`，避免 5 份文档同时「有效」。
- 每次审查必须先做一次「环境核实 + 待验证定格」，否则标为「静态审查」，不得据其排定 P0。

### 8.2 代码护栏（复用项目已有的 AST 扫描习惯）

项目已有「AST 扫全仓 `AppException` 必须带码」的先例，同样手法可以低成本挡住复发：

| 规则 | 拦什么 |
| --- | --- |
| 禁止 `*.flow_points =` 直接赋值 | 余额读改写（RF-015 类） |
| 简历写入必须经 `persist_resume_data` | 第五条写路径（RF-013 类） |
| 跨服务请求体必须含 `turn_id` | 回合计费断链（RF-007 类） |
| 长度/超时常量必须来自 `contracts` | 参数各处各写一个（RF-036 类） |
| 渲染 URL 必须经 `browser_url`/`render_url` 二选一显式声明 | 地址混用（RF-052 类） |
| 新增管理端写操作必须带幂等键 | 重复发放（RF-028/065 类） |

同时把这些写成 `AGENTS.md` 的硬规则（现有 §后端约束已有类似表述，需要具体到函数名）。

### 8.3 发布门禁

启动自检（§6.4 第 3 条）+ 一个 5 步 smoke 脚本：登录 → 保存 → 一轮对话（含 apply）→ 导出 PDF → 打开分享页。每一步都断言「无 5xx、无 `127.0.0.1`、有 task 行、扣费差额为 0」。

### 8.4 运营保障（本轮审查的空白，商业化前必补）

1. **备份与恢复演练**：25 表无物理外键 + 对象存储「删除只清索引」的形态，恢复顺序错一步就产生悬挂引用。需要一次真演练（含 MinIO 对象与 `uploaded_files` 对齐）。
2. **对账日报**：资金与任务的日差额，超过阈值告警（配合 §6.5）。
3. **合规**：简历即个人信息。分享页脱敏不全（RF-056）+ 自定义短 token（RF-059）+ JWT 进 URL（RF-025）三条叠加，在合规上比技术面更严重；建议把「公开分享的数据范围」写成产品条款，而不是只做技术修补。

---

## 9. 明确不做的事

| 不做 | 理由 |
| --- | --- |
| 拆微服务 / 引消息队列 | 单实例、单 worker 下清扫器 + 唯一约束足够；引入 MQ 会把「没有回合身份」的问题搬到一个更难调的地方 |
| 上多副本 / 水平扩容 | M2/M3 未完成前扩容会把 PDF 进程锁、faux 单例、内存态证据、SSE 中继同时放大（报告 §10.6 同结论） |
| 改 JSON Patch 协议 | `revision` + 整包已能消除覆盖问题；字段级 patch 的成本远高于收益 |
| 重写 Pinia 结构 | 前端状态库分层是合理的，问题在「保存协议」不在「状态容器」 |
| 追 66 条全绿 | 目标是让 40 条失去存在条件；26 条独立点按常规迭代走，不必挤进发布前 |
| 大范围重构 `ai_chat_service`/`ResumeEditor` | 收益 < 风险；只在其上做机制改造，拆分留给有测试保护之后 |

---

## 10. 时间轴与依赖

```
S0 定格事实（0.5d）
   └─> S1 止血（1w）───────────┐
        ├─ M1 写入语义（1w）────┼─> M5 可证明（与 M2/M3 同步）
        ├─ M4 参数收敛（3d）────┤
        ├─ M6 信任模型（穿插）──┤
        └─> M2 回合身份（1w）──> M3 生命周期（1w）──┘
                                     └─> S3 防复发（第 4 周起持续）
```

- **M2 必须先于 M3**：没有 `turn_id`，租约退款只能按「任务」退，退错也发现不了。
- **M5 与 M2/M3 同步做**，不要留到最后：它是前两者的验收工具。
- **M4 最便宜**，随时可插队；S1 里的第 5、6 项其实就是 M4 的第一次落地。

---

## 11. 需要向上说明的四个判断

1. **P0 是否成立取决于部署形态**，不是取决于代码。RF-003 若命中就是完全账户接管，若部署正确则只是「缺一道保险」。先花半天核实，再对外承诺发布时间。
2. **6.6 分主要被五个参数拖累**（池、上传、渲染地址、标题长度、超时），不是被普遍性缺陷拖累。这意味着「修 5 个参数」的性价比高于「做一个新功能」，排期上应当被允许插队。
3. **当前架构适合纵向加功能，不适合横向扩容**。任何「多副本/多机」的需求，都应排在 M2+M3 之后。
4. **商业化前必须补的不是功能，是三件运营能力**：对账日报、备份恢复演练、公开分享的数据范围（合规）。

---

## 附：与前序文档的关系

| 文档 | 关系 |
| --- | --- |
| `code-review/00`～`04` | 事实来源；本文引用其证据位置，不重复描述 |
| `code-review-report.md` | 缺陷清单与评分；本文在其之上做归因、分层与验收设计 |
| `docs/reviews/2026-11-16-code-review.md` | 上一轮（P0 已修）；本文的 §8.2 护栏规则可反向补进那份文档的「防复发」空白 |
