# 全仓代码审查（2026-11-16）

审查范围：`backend/app`（18.7k 行）、`frontend/src`（12.6k 行）、`ai-service/src`（9.3k 行），以及 `docker-compose.yml`、`frontend/nginx.conf`、各 `.env.example`。

验证基线（本次实跑）：

| 检查 | 结果 |
| --- | --- |
| `backend: pytest tests -q` | **356 passed**（含 31 subtests） |
| `frontend: npx vue-tsc --noEmit` | 通过，无类型错误 |
| `frontend: npx vitest run` | **130 passed / 1 failed**（见 P2-1） |
| `ai-service: tsc --noEmit` | 通过 |
| `ai-service: npm test` | **172 passed / 0 failed** |

总体判断：工程质量高于同类项目平均水平——分层清楚（api / services / core）、注释解释「为什么」而不是复述代码、异常与响应体统一、越权路径普遍收敛到 `get_resume` 一处。下面的问题集中在**边界处**（代理头、跨进程取资源、后台配置进前端 HTML、并发扣费），属于「功能都对了，但攻击面和并发边界还没收口」。

---

## P0 高危：伪造 `X-Forwarded-For` 可绕过登录限频与兑换码 IP 限制

`get_client_ip()` 无条件相信客户端可写的 `X-Forwarded-For` 首段：

```python
# backend/app/core/request_utils.py:8-11
for header in ("x-forwarded-for", "x-real-ip", "cf-connecting-ip", "x-client-ip"):
    value = request.headers.get(header)
    if value:
        return value.split(",", 1)[0].strip()[:64]
```

而前置 Nginx 用的是**追加**语义：

```nginx
# frontend/nginx.conf:25
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
```

于是攻击者自带 `X-Forwarded-For: 1.2.3.4`，到达后端时变成 `1.2.3.4, <真实IP>`，取首段就拿到自己编的值。三个真实后果：

1. **登录爆破限频失效**：`throttle_id = f"{email}|{get_client_ip(request)}"`（`backend/app/api/auth.py:124`）。每次请求换一个假 IP，就换一个计数器，`AUTH_LOGIN_MAX_ATTEMPTS=5` 的锁定永远命中不了。
2. **兑换码「IP 只能兑换一次」失效**：`redeem_flow_points(db, user, code, get_client_ip(request))`（`backend/app/api/ai.py:671`）拿它和 `FlowPointRedeemRecord.ip_address` 比对（`core/flow_points_service.py:499-509`）。
3. **审计字段失真**：`register_ip`、`last_login_ip` 同样来自这里（`auth.py:81`、`auth.py:137`），事后追溯不可信。

修复建议（选一，推荐前两条都做）：

- Nginx 改覆盖语义：`proxy_set_header X-Forwarded-For $remote_addr;`；真实结算类接口另用专门的 `X-Real-IP`（当前已由 `$remote_addr` 正确设置）。
- `get_client_ip` 改为「只信任受信代理」：配置 `TRUSTED_PROXY_CIDRS`，仅当 `request.client.host` 落在其中时才读 `X-Real-IP` / XFF 的**最右一跳**（代理追加的那一跳），否则一律回落 `request.client.host`。
- 补一条回归：同一账号、每次换 XFF，第 6 次仍应 429。

---

## P1 中危

### P1-1 PDF 导出链路的 SSRF（用户可控 URL 由后端发起请求）

`basics.avatar` 只是一个长度上限 500 的字符串（`backend/app/core/resume_schema.py:93`），没有任何 URL 约束；渲染时仅当它以 MinIO 前缀开头才改写，其余原样进 `<img src>`（`backend/app/services/preview_service.py:192-195`）。富文本白名单同样放行 `<img src="http://…">`（`preview_service.py:24` → `rich_text_service.py` 的 `allow_images`）。

真正危险的是两条渲染路径都会**由后端主动去取这个 URL**：

```python
# backend/app/services/pdf_export_service.py:340-349（WeasyPrint）
def _pdf_url_fetcher(url: str):
    object_name = _get_uploaded_object_name(url)
    if object_name: ...
    return _fetch_external_url(url, default_url_fetcher)   # 任意 URL 兜底

# backend/app/services/pdf_export_service.py:352-361（Chromium）
def _fulfill_uploaded_file_route(route) -> None:
    object_name = _get_uploaded_object_name(route.request.url)
    if not object_name:
        route.continue_()                                  # 浏览器真的去请求
```

任意注册用户都能把头像（或描述里的图片）指向 `http://127.0.0.1:3306/`、`http://minio:9000/`、`http://169.254.169.254/latest/meta-data/`，导出 PDF 时由后端容器替他去请求内网；WeasyPrint 的默认 fetcher 还支持 `file://` 与 `data:`，属越权读面。项目里已有 `core/url_guard.py` 这套 SSRF 防护，但**没有用在渲染链路上**。

修复建议：

- 写入侧：`normalize_resume_document` 里校验 `basics.avatar` 必须匹配 `storage_public_url` 前缀（或直接只存对象名），富文本里的 `<img src>` 在 `sanitize_rich_text_html(allow_images=True)` 时同样只放行 storage 主机。
- 渲染侧：`_pdf_url_fetcher` / `_fulfill_uploaded_file_route` 对非 storage 的 URL 先过 `probe_public_url()`，只放行公网 http(s)，显式拒绝 `file:`/`data:`（图片用 storage 读）。

### P1-2 后台设置是「绕过清理链路」的存储型 XSS 入口

项目既有规则是「富文本进 HTML 前必须过清理」，公告做到了（`backend/app/api/admin/announcements.py:74`、`:102`），但系统设置没有：

```python
# backend/app/api/admin/settings.py:31,39
set_setting(db, "ai_records_hint", payload.ai_records_hint.strip(), ...)
set_setting(db, "user_agreement", payload.user_agreement.strip(), ...)
```

这两个字段在三个地方直接进 `v-html`：

- `frontend/src/views/Login.vue:372` —— **未登录访客**都能看到（用户协议）
- `frontend/src/views/AiRecords.vue:355` —— 全站可见（`ai_records_hint`）

写入是管理员权限，但「管理员能写 HTML」不等于「该字段可以不清理」：任一管理员账号失陷、或后台接入第三方运营同学，都会变成对所有访客生效的持久化 XSS。

修复建议：写入时套用 `sanitize_rich_text_html(value, allow_images=True)`（与公告同一条链路）；前端再加一层 DOMPurify（`Admin.vue:59` / `Profile.vue:45` 的 `sanitizeFeedbackHtml` 已经实现，上提成 `utils/` 复用即可）。

### P1-3 积分扣减是读-改-写，没有行锁（并发少扣 / 多扣）

```python
# backend/app/services/flow_points_service.py:309-315
current_balance = point_amount(user.flow_points)   # 读
next_balance = point_amount(current_balance + points_delta)
user.flow_points = next_balance                    # 整体覆盖写
```

同一用户并发两个 AI 回合（前端不拦，多标签页即可触发）时，两个 Session 都读到 100，各自写回 95 —— **只扣了一次**。`settle_task_flow_points`（`:340`）与 `consume_flow_points`（`:405`）走的是同一条路径。全仓只有兑换码那一处加了 `with_for_update()`（`:484`），用户余额没有。

修复建议：

```python
# 原子扣减，余额不足靠 rowcount 判定
rows = db.execute(
    update(User)
    .where(User.id == user.id, User.flow_points >= charge)
    .values(flow_points=User.flow_points - charge)
).rowcount
if rows == 0: raise AppException("Flow Points 不足…", 402)
```

或用 `select(User).where(...).with_for_update()` 锁用户行后再算差额（退款方向要允许负数，见 `allow_overdraft_to_zero`）。

### P1-4 流式回合里跑同步 SQLAlchemy 与同步 MinIO 读（阻塞事件循环）

流式生成器是 `async def iterator()`，但它内部调用的是同步 Session 与同步存储读：

```python
# backend/app/services/ai_service_client.py:81（在 async 函数里）
content, _ = read_uploaded_file(object_name)   # 同步 HTTP 打 MinIO/OSS
```

`_stream_ai_service_reply` 里每一轮的 `db.commit()`、`build_ai_service_context()` 同样如此（`backend/app/services/ai_chat_service.py:688`、`:894`）。7 个 async 路由中这一条是长任务路径，阻塞会直接放大首字延迟——这与仓库里记录过的「空闲后首字延迟 10s+」是同一类问题。

修复建议：这类调用用 `await anyio.to_thread.run_sync(...)` 包一层（或至少把 `read_uploaded_file` 包掉），把线程池当 IO 边界；不需要改成全异步 ORM。

---

## P2 低危 / 工程债

| # | 问题 | 证据 | 建议 |
| --- | --- | --- | --- |
| P2-1 | **前端 1 个测试是红的**：`surfaces a resume list failure instead of leaving the action disabled` 期望弹窗文本含 `boom`，而组件已改为「非中文错误一律回退友好文案」 | `frontend/src/views/__tests__/WorkspaceInfo.spec.ts`（`expect(dialog.text()).toContain("boom")`）vs `WorkspaceInfo.vue:123-129` 的 `errorMessage()` | 断言改成「简历列表读取失败」，并给 `errorMessage` 补单测锁定这条语义；不要为了让测试绿而回退文案 |
| P2-2 | **按中文子串判断 HTTP 语义**：`msg.includes("Flow Points") \|\| msg.includes("点数")` 决定是否弹全局 toast；同一段里 `err.message.includes("JSON")` 会把「报文里含 JSON 字样」的真实错误吞成原文 | `frontend/src/api/ai.ts:180-195` | 改用 `response.status === 402`；错误体统一走一个 `parseErrorBody()`，去掉字符串嗅探 |
| P2-3 | **同一份逻辑多份实现**：`errorMessage` 在 WorkspaceInfo / WorkspaceSettings 各一份；`sanitizeFeedbackHtml` 在 Admin / Profile 各一份；API base 字面量在 `api/request.ts:5` 与 `api/ai.ts:147` 各一份（漏配 `VITE_API_BASE_URL` 时静默打 `127.0.0.1:8000`） | 见左 | 上提到 `utils/`，API base 只留 `request.ts` 一处导出 |
| P2-4 | 流式请求无法取消：`postAiStream` 没有 `AbortController`，切简历/切页后 reader 仍在跑并继续写 store | `frontend/src/api/ai.ts:149-241` | `send`/`regenerate` 持有 controller，`onBeforeUnmount` / 切简历时 abort |
| P2-5 | `_pdf_locks` 只增不减（每份简历一个 Lock）；`generate_redeem_codes` 把整张 code 表读进内存 | `pdf_export_service.py:28-29,427-434`；`flow_points_service.py:583` | 锁用 `WeakValueDictionary` 或按 fingerprint 加 TTL；兑换码改用 `WHERE code IN (...)` 局部查重（`import_redeem_codes` 已是正确写法） |
| P2-6 | `.env.example` 与 `Settings` 不同步：`AI_CHAT_CHANGE_TIMEOUT`、`AI_MODEL_IO_LOG_ENABLED`、`AI_MODEL_IO_LOG_PATH`、`AI_MODEL_IO_LOG_MAX_CHARS` 在用但未登记 | `backend/app/core/config.py:89-92` | 补进 `.env.example`（AGENTS.md 明文要求） |
| P2-7 | 30 处 `raise AppException(...)` 没给 code，一律按 500 返回；其中「AI 修改结果不是有效的简历数据」这类是客户端可纠正的错误 | `backend/app/core/exceptions.py:51` + `grep "raise AppException(" \| grep -v ", [0-9][0-9][0-9])"` | 校验类统一 400/422，第三方不可用 502/503 |
| P2-8 | `Admin.vue` 1599 行仍是巨型组件：用户 / 简历 / AI 任务 / 导出 / 模板 / 反馈 / 积分表格全部内联，只把 dashboard、公告、AI 设置抽了出去 | `frontend/src/views/Admin.vue` | 按已抽出的模式继续拆成 `Admin*Panel.vue`，每个面板自带取数与分页（这正是 AGENTS.md「组件封装与拆分」的要求） |
| P2-9 | **测试覆盖缺口**：`/api/shares/*`、`/api/templates/*`、`/api/files/*`（上传与对象代理）、`/api/auth/register`、`/api/auth/reset-password`、`/api/feedback`、`/api/resumes/{id}/export/pdf` 均无测试；admin 侧只有路由符号反射检查 | `backend/tests/` | 优先补上传归属与分享失效两类（都是越权/信息泄露面） |
| P2-10 | 类型宽松：前端 184 处 `any`、69 处 `catch (err: any)`（TS 4.4+ 应为 `unknown`）；`utils/aiDiff.ts` 单个文件 22 处 `any` | `grep -rn ": any\b\|as any\b" frontend/src` | 新代码一律 `unknown` + 收窄；`aiDiff` 可以按 section 定义判别联合 |
| P2-11 | 死代码/待恢复：`services/word_export_service.py`（192 行，只被 `exports.py` 的注释提到）、`services/ai/` 目录仅剩 `token_usage.py`、`exports.py` 的 410 接口 | 见左 | 已有文档说明是有意保留；建议在文件头补一行「恢复条件」注释，避免下次被当垃圾清理 |
| P2-12 | `/health` 未鉴权且回显 `provider`（`faux`/`live`） | `ai-service/src/server.ts:110-113` | 只回 `{status:"ok"}`，provider 放进受令牌保护的 `/v1/catalog` |

---

## 值得保留的做法（别在重构中弄丢）

- **异常出口单一**：`register_exception_handlers` 把校验错误翻译成中文，生产环境不回显 `str(exc)`（`core/exceptions.py:70-78`），`debug_enabled` 强制在 prod/staging 关闭。
- **越权与不存在同码**：`get_resume` 一律 404（`resume_service.py:61-66`）；私有对象未登录与「不是你的」都回 404（`api/files.py:83-87`），不泄露对象存在性。
- **服务间鉴权 fail-closed**：缺令牌时后端拒发（`ai_service_client.py:115`）、Node 侧拒收（`server.ts:84-86`），比较用 `hmac.compare_digest` / `timingSafeEqual`。
- **BYOK 密钥治理**：独立口令 + `kid` 多版本轮换 + 生产拒绝弱默认（`core/crypto.py`），接口只回掩码。
- **模板注入面收得紧**：Jinja `autoescape` + 字体按预设键查表（`preview_service.py:99-110`）+ 富文本 bleach 白名单，注释里写清了为什么放弃「逐项清洗 font stack」。
- **计费链路可对账**：预扣 → 结算 → 退款都走 `FlowPointTransaction` 账本，`points_used` 由净额反推（`_set_task_usage_from_net_points`），失败回合只落 failed 不扣费。
- **测试密度**：AI/计费/待确认变更这类高危路径有 356 条后端断言 + 172 条 Node 断言，且测试命名描述行为（如 `surfaces a resume list failure instead of leaving the action disabled`）。

---

## 建议的修复顺序

1. **P0**：修 `get_client_ip` + Nginx XFF + 补一条限频回归（收益最大、改动最小）。
2. **P1-1 / P1-2**：两处都是「用户/后台可控内容进 HTML 或进后端请求」，一起改能顺带把 storage URL 约束做扎实。
3. **P1-3**：积分改成原子 UPDATE，先在 `add_point_transaction` 一处收口。
4. **P2-1 / P2-2**：把红的测试修绿、把字符串嗅探换成状态码，避免下一次改动再被静默吞掉。
5. **P2-8 / P2-9 / P2-10**：结构性债，按「拆一个面板补一组测试」的节奏推进，不要一次性大改。

---

## 修复进度

### 第一批（2026-11-16，已完成）

| 编号 | 修复内容 | 改动文件 | 回归测试 |
| --- | --- | --- | --- |
| P0 | 代理头只在直连方是受信代理时采信，且从右往左取第一个非受信跳；Nginx 用 `$remote_addr` **覆盖** `X-Forwarded-For` | `backend/app/core/request_utils.py`、`backend/app/core/config.py`、`frontend/nginx.conf` | `backend/tests/test_request_utils.py`（10 条，含「伪造 XFF 不得拆分限频计数」） |
| P1-1 | PDF 渲染只允许内联 scheme / 自家上传对象 / 公网 http(s)；存储读失败改为空白图，不再兜回真实请求；Chromium 侧对非白名单请求 `abort` | `backend/app/services/pdf_export_service.py`、`backend/app/core/url_guard.py` | `backend/tests/test_pdf_asset_guard.py`（13 条） |
| P1-2 | `user_agreement` / `ai_records_hint` 读、写两侧都过 bleach 白名单（写防新增，读清存量） | `backend/app/services/app_settings_service.py` | `backend/tests/test_settings_rich_text.py`（8 条） |
| P1-3 | 余额加减交给数据库：`UPDATE ... WHERE flow_points + delta >= 0`，扣费不足看 `rowcount`；不再把结果赋回 ORM 对象 | `backend/app/services/flow_points_service.py` | `backend/tests/test_flow_points_atomicity.py`（6 条，含「DB 已扣到 95 时以 95 为基准」） |
| P1-4 | 附件字节读取改走 `anyio.to_thread.run_sync`，不再卡事件循环 | `backend/app/services/ai_service_client.py` | `backend/tests/test_ai_service_client.py::AttachmentReadOffloadTest` |
| P2-1 | 红测试按组件现有语义改写，并补一条「中文错误原样透传」锁定 `errorMessage` | `frontend/src/views/__tests__/WorkspaceInfo.spec.ts` | 同文件 13 条 |
| P2-2 | 402 改按状态码判定，去掉 `includes("Flow Points")` / `includes("JSON")` 嗅探；`SILENT_ERROR` 魔法字符串换成 `SilentStreamError` | `frontend/src/api/ai.ts`、`ResumeCreateFlow.vue`、`useResumeChatStream.ts` | `frontend/src/api/__tests__/ai.spec.ts`（5 条，含英文 402 也弹 toast） |
| P2-6 | 补 `TRUSTED_PROXY_NETWORKS`、`AI_CHAT_CHANGE_TIMEOUT`、`AI_MODEL_IO_LOG_*` 到 `.env.example` 与根 README | `backend/.env.example`、`README.md` | — |

实跑基线：后端 395 passed（原 356）、前端 137 passed（原 130 + 1 红）、`vue-tsc` 0 错、`vite build` 通过。

### 仍未处理

P2-3（重复实现上提）、P2-4（流式请求无 AbortController）、P2-5（PDF 锁与兑换码全表读）、P2-7（30 处 `AppException` 未给 code）、P2-8（`Admin.vue` 拆分）、P2-9（shares / templates / files 等测试缺口）、P2-10（`any` 收敛）、P2-11（死代码注释）、P2-12（`/health` 回显 provider）。
