# Phase 1：ResumeFlow Backend 深度代码审查

> 审查基线：`a8d451c`（2026-11-20）
> 审查对象：`backend/app` 全部模块（api / core / models / schemas / services / templates）+ `alembic/` + 依赖与部署配置 + 524 个后端测试。
> 审查方式：**只读**。逐函数追踪核心接口的完整调用链，并对照 SQLAlchemy 模型与实际迁移、对照 API 与前端/AI 服务的字段契约。
> 未执行：并发压测、真实 OCR/PDF 渲染、数据库写入、生产配置核验。

> **关于编号**：本报告使用 `B-xx` 独立编号（Backend 专项）。与 Phase 5 最终审计重叠的条目在「关联」里标注 `RF-xxx`；本轮新增、前序报告未覆盖的条目会明确写「新增」。P0/P1/P2/P3 按实际影响排序，不以「前序报告说过」为依据。

---

## 1. Backend 总体评价

### 1.1 结论先行

Backend 是这份代码库里**工程纪律最好的一层**：异常码有统一约定（`AppException` + 4xx/5xx 映射）、输入有 Pydantic 约束、资源访问一律走 `get_resume(user_id, id)`、危险渲染有白名单（模板配置、富文本、对象名、PDF 资源）、迁移链干净（43 个版本、单头、revision id 全部 ≤32 字符）。

它的问题不在「实现质量」，而在**运行形态假设**：

1. **单进程 + 默认连接池**（5+10）与「请求内做几十秒同步工作」（PDF 渲染、OCR、导入）直接冲突，会把整站拖进连接等待。
2. **一个 `pdf_base_url` 同时服务 Chromium 和浏览器**，导致远程部署下预览/分享的头像与字体指向 `127.0.0.1:8000`。
3. **上传先 `read()` 再判大小**，内存峰值不受控。
4. **写简历没有并发控制**（P0 的另一半在前端），后端只提供「整份覆盖」。

### 1.2 分层评分（backend 视角）

| 维度 | 评价 | 依据 |
| --- | --- | --- |
| API 与鉴权 | 强 | 角色校验集中、资源归属统一、注册/登录限频与 bcrypt 长度守卫 |
| 数据模型 | 中上 | 索引基本覆盖热查询；缺组合索引；无物理外键 |
| 事务边界 | 中 | 单资源写入干净；跨资源（信息库+简历）部分成功；流式接口自持会话 |
| 并发控制 | 弱 | 只有兑换码与版本恢复有锁；简历写入、白名单版本去重均无 |
| 异步与阻塞 | 弱 | 同步 OCR / Chromium / SMTP 全在请求线程里 |
| 渲染安全 | 强 | 四层白名单，SSRF 有 DNS 校验与资产拦截 |
| 可观测性 | 中 | 异常与访问日志规范；缺慢查询、池等待、任务积压指标 |
| 测试 | 强（后端） | 524 用例，重点覆盖计费、越权、PDF 资源、导入导出 |

---

## 2. 模块审查记录

### 2.1 `core/`

| 模块 | 结论 |
| --- | --- |
| `config.py` | 配置项齐全，`debug_enabled` 在生产强制关闭；**JWT 密钥无独立强度校验**（B-01） |
| `database.py` | 仅 `pool_pre_ping + pool_recycle`，未设 `pool_size/max_overflow/pool_timeout` → 默认 5+10/30s（B-02） |
| `security.py` | JWT 只含 `sub+exp`；`get_current_user` 校验 `status`；`get_current_admin` 走 DB 角色；**无吊销机制**（关联 RF-027） |
| `exceptions.py` | 4xx/5xx 映射清晰，422 有中文友好文案，500 隐藏内部细节——**正面** |
| `redact.py` | 日志脱敏覆盖 Authorization/api_key/token 查询串——**正面** |
| `throttle.py` | 登录/验证码失败计数，Redis 故障 fail-open 并告警——设计合理，但**只有「邮箱+IP」维度**（B-17） |
| `request_utils.py` | 受信代理从右往左取 IP，nginx 覆盖而非追加——**P0 级问题已修复** |
| `redis.py` | 单例客户端，socket 超时 3s——合理 |
| `resume_schema.py` | 字段清洗、长度上限、id 去重、自定义模块规范化——**正面**，是「同一份简历一种形状」的关键 |
| `template_config.py` | 颜色/字号/边距/字体全白名单——**正面**，阻断了 CSS 注入 |

### 2.2 `api/`

| 路由 | 审查结论 |
| --- | --- |
| `auth.py` | 注册锁（Redis nx + 值校验删除）、邮箱/用户名唯一约束兜底、密码 72 字节守卫；**改密/重置不失效旧 token**（RF-027） |
| `resumes.py` | CRUD/复制/导入/预览；`import-file` 是同步长任务（B-04）；`preview-pdf` 期间持有连接（B-02） |
| `ai.py` | 代理 + 计费 + 工具回调；身份只靠静态令牌（关联 RF-008），`usage` 无幂等（RF-007） |
| `user_ai.py` | BYOK CRUD、技能包导入（zip 安全做得扎实）、远程模型探活（SSRF 已在 Phase 2 覆盖） |
| `files.py` | 上传/代理；私有前缀鉴权 + 对象名白名单——**正面**；上传内存峰值见 B-03 |
| `exports.py` | PDF/JSON 导出；Word 已 410 下线且注释说明原因——**正面**；PDF 响应无 `no-store`（B-16） |
| `shares.py` | `no-store`、过期/停用校验、脱敏开关；脱敏只覆盖 `basics`（B-14） |
| `announcements.py` | 已读表唯一约束 + 缓存版本失效；并发首读有竞态（B-07） |
| `feedback.py` | 服务端 bleach 后才入库/发信，邮件正文二次转义——**正面**；500k 字符上限 + 无频率限制（B-21） |
| `avatars.py` | 跨用户删除当 404，不泄露存在性——**正面** |
| `admin/*` | 10 个路由模块全部挂 `get_current_admin`；敏感值只回掩码——**正面** |

### 2.3 `models/` 与 `alembic/`

- 25 张表，**无物理外键**，删除靠显式级联（`delete_resume`）。
- 唯一约束覆盖了业务上必须唯一的位置：`users.email/username`、`resume_templates.template_id`、`ai_model_configs.model_ref`、`flow_point_redeem_codes.code`、`flow_point_redeem_records(code_id,user_id)`、`announcement_reads(announcement_id,user_id)`、`user_skills(user_id,slug)`、`ai_agent_traces.trace_id`。
- 组合索引：`resume_versions(resume_id, create_time)`、`info_fragments(user_id,kind)`、`info_fragments(user_id,update_time)` 已建；**`ai_tasks`、`flow_point_transactions`、`resumes` 缺 `(user_id, create_time/update_time)` 组合索引**（B-09）。
- 迁移：43 个版本、单头 `0043`、无缺失父版本、revision id 全部 ≤32 字符（我脚本扫过）；`0035/0042` 等对已存在列做了幂等判断；`0043` 的回填是单条 UPDATE 且 downgrade 明确标注不可逆——**正面**。
- 迁移与模型一致性抽查：`is_pinned`/`context_summary`/`summary_message_id`/`timing`/`feature`/`resume_versions` 索引均有对应迁移，无漂移。

### 2.4 `services/`

| 服务 | 结论 |
| --- | --- |
| `resume_service` | 创建/更新/删除/复制/版本；**标题长度与列宽不一致**（B-06）、**版本去重无并发保护**（B-08） |
| `ai_chat_service` | 三段式提交（RF-040）、apply 整包（RF-002）、消息序列化含全量快照（B-10） |
| `pdf_export_service` | 指纹缓存 + 资产白名单 + 临时文件清理——**正面**；每次渲染新起 Chromium（B-02 相关） |
| `preview_service` | 统一渲染入口，`_prepare_resume_data` 负责语言/布局/头像；头像地址见 B-05 |
| `storage_service` | 前缀白名单 + 归属集合 + 穿越拦截 + 私有缓存头——**正面**；上传内存与 DB 一致性见 B-03/B-17 |
| `info_fragment_service` | 每条 commit、去重靠指纹查询、上限 check-then-insert（B-12） |
| `template_service` | 静态清单同步 DB；`/templates` 每次请求渲染 12 份预览（B-11） |
| `announcement_*` | 读写双侧净化 + 版本化缓存——**正面** |
| `app_settings_service` | 富文本设置在读、写两侧都过白名单——**正面**（这是我本轮专门验证的一个潜在 XSS 点，结论是不成立） |
| `email_service` | SMTP 10s 超时、反馈正文 sanitize + escape——**正面**；同步发送（B-18） |
| `resume_import_service` | 无文件大小/页数/像素/OCR 超时限制（B-04） |
| `user_ai_service` | 技能包 zip 解压五重防护、BYOK Fernet 密钥环——**正面** |
| `word_export_service` | 实现存在但路由已下线，注释与行为一致——**正面** |

### 2.5 `templates/`

- Jinja `select_autoescape(["html","xml"])`；富文本只在 `|rich_text|safe` 处放开，滤镜是 bleach 白名单；图标宏只按固定 SVG 分支输出，`name` 不参与拼接。
- 字体走 `@font-face` URL，不内联 base64（避免每份 HTML 膨胀）。
- **但字体与头像 URL 都基于 `pdf_base_url`**（B-05），这是本轮最重要的新发现。

---

## 3. 问题清单

### P0

#### B-01 生产环境弱默认 JWT 密钥缺少独立校验（已确认，条件触发）

- **文件/函数**：`app/core/config.py:8,55`；`app/core/crypto.py:91-107`；`app/main.py:44-52`
- **关联**：RF-003（本轮从 backend 视角补全根因）
- **触发**：`APP_ENV=production` + 配置了 `BYOK_ENCRYPTION_KEYS` + `JWT_SECRET_KEY` 仍是默认值。
- **根因**：唯一的启动强校验 `validate_encryption_config` 在 `mode=dedicated` 时直接 `return ""`，它保护的是 BYOK 密文，不是 JWT 签名密钥。
- **影响**：可用已知密钥签出任意 `sub` 的令牌，等于任意账户（含已提升的管理员）接管。
- **修复**：在生产环境**无条件**校验 `JWT_SECRET_KEY`（非默认、长度 ≥32 字节），与 BYOK 配置解耦；启动日志打密钥指纹。
- **验证**：`APP_ENV=production` + 独立 BYOK 密钥 + 默认 JWT 密钥启动，断言应拒绝；用默认密钥签 token 调 `/auth/me` 断言 401。
- **状态**：已确认（代码路径），是否存在于实际部署**待验证**。

### P1

#### B-02 请求级 Session 跨长耗时同步操作持有连接，默认连接池可被 15 个并发导出打满（已确认）

- **文件/函数**：`app/core/database.py:9`（无 pool 参数）；`app/api/resumes.py:438-441`（`preview_pdf`）、`app/services/pdf_export_service.py:578-608`（`get_pdf_path`）、`:611-625`（`export_pdf`）
- **触发**：多个用户同时打开 PDF 预览或导出（每次渲染 5–60s），或并发导入。
- **根因**：`get_resume()` 的 SELECT 在 SQLAlchemy 中开启事务并占住连接，直到请求结束才由 `Session.close()` 归还；而渲染发生在这之后。默认池 `pool_size=5, max_overflow=10, pool_timeout=30`。
- **影响**：15 个并发 PDF 请求即耗尽连接池，其余所有请求排队 30s 后抛 `QueuePool limit ...` → 全站 500。这与「单 worker」叠加，恢复要等渲染结束。
- **修复**：① 渲染前 `db.commit()` 或显式 `with SessionLocal()` 短会话读取数据后关闭；② 显式配置 `pool_size/max_overflow` 并设置较短的 `pool_timeout`；③ 将 PDF 生成移出请求线程（后台任务 + 轮询/流式）。
- **验证**：把 `pdf_render_timeout_ms` 调到 10s，并发 20 个 `/preview-pdf`，同时请求 `/resumes`，观察 500 与耗时。
- **状态**：已确认（连接生命周期与池默认值均可从代码确定；未压测）。

#### B-03 三个上传接口先 `read()` 全量入内存、后校验大小（已确认）

- **文件/函数**：`app/services/storage/storage_service.py:161-196`（avatar）、`:199-229`（公告）、`:231-260`（对话图片）
- **触发**：任意登录用户上传超大文件；compose 同时发布 8000 端口，绕过 nginx 的 25MB 限制时无上限。
- **根因**：`content = file.file.read()` 在 `len(content) > MAX_SIZE` 之前执行；未读 `Content-Length`；Starlette 只对大文件做磁盘 spool，`.read()` 仍全量载入内存。
- **影响**：单个 1GB 上传即可让单进程 OOM，伴随所有在途请求失败；即使有 nginx，40 个 25MB 并发上传也有 ~1GB 峰值。
- **修复**：先读 `file.size`（Starlette 提供）或 `Content-Length` 预判并拒绝；分块读取并在超过上限时中断；给上传路由单独限流。
- **验证**：`curl --data-binary @1GB.bin` 打 `/api/files/upload-avatar`，观察 RSS。
- **状态**：已确认（代码顺序确定）。

#### B-04 导入链路无大小/页数/像素/OCR 超时上限，且全同步（已确认）

- **文件/函数**：`app/api/resumes.py:116-220`；`app/services/resume_import_service.py:38-52`（PDF）、`:70-108`（OCR）、`:118-170`
- **触发**：上传超长 PDF / 超大图片 / 触发 tesseract 卡死。
- **根因**：`_extract_pdf` 逐页 extract 无页数上限；`_extract_image` 无像素上限（Pillow 的 bomb 保护会抛异常被归为 400，但解码峰值已在内存里）；OCR 依次尝试 4 组语言，`pytesseract` 子进程**无超时**。
- **影响**：一个 5000 页 PDF 或一张 8000×8000 图片可让线程池中的一个线程占用数分钟；OCR 挂起时线程永久占用，最终耗尽 40 线程池。
- **修复**：`Content-Length` + 页数（≤30）+ 像素（≤40MP）+ 单文件大小上限；`pytesseract` 加 `timeout=`；导入整体加 deadline。
- **验证**：构造 200 页 PDF 与 8000px 图片，观察请求耗时与线程池占用。
- **状态**：已确认（限制缺失）；实际耗时**待测**。

#### B-05 一个 `pdf_base_url` 同时供 Chromium 与浏览器使用，远程部署下预览/分享的头像与字体指向 `127.0.0.1`（已确认，新增）

- **文件/函数**：`app/core/config.py:121`；`docker-compose.yml:159`（`PDF_BASE_URL: http://127.0.0.1:8000`）；`app/services/preview_service.py:186-196`；`app/services/avatar_service.py:54-67`；`app/services/preview_service.py:117-134`（`font_face_css`）
- **触发**：任何非「浏览器与后端同机」的部署（即所有真实部署）打开简历预览或公开分享页。
- **根因**：`_prepare_resume_data` 把 `basics.avatar` 解析成 `render_url()`（`{pdf_base_url}/api/files/...`），字体 `@font-face` 的 `src` 也用 `pdf_base_url`。这个值是为容器内 Chromium 设计的（`127.0.0.1:8000`），但同一份 HTML 会直接作为 `srcdoc` 交给浏览器。
- **影响**：远程用户的浏览器去请求**自己机器**的 8000 端口 → 头像裂图、字体回退；而 PDF 里一切正常，形成「预览和导出不一致」。分享页同理，访客看到的是无头像、字体不同的简历。
- **修复**：渲染层接收 `base_url` 参数，PDF 传内网地址、预览/分享传公开地址；或输出同源相对路径（`/api/files/...`）让浏览器与 Chromium 各自解析（Chromium 用 `base_url` 兜底）；分享页也可改为相对路径。
- **验证**：在 compose 环境用另一台机器访问 `http://server:8080`，打开含头像的简历预览，看 Network 是否请求 `127.0.0.1:8000` 且失败。
- **状态**：已确认（配置与调用链确定）；线上是否已发生**待验证**。

#### B-06 简历标题长度在 schema / service / 列宽之间不一致，复制必然 500（已确认）

- **文件/函数**：`app/models/resume.py:17`（`String(100)`）；`app/schemas/resume.py:144,152,191`（创建/更新无上限，复制 120）；`app/services/resume_service.py:94,155`（创建原样写入；复制 `[:120]`）
- **触发**：标题 101–120 字时复制；或创建/更新时标题 >100 字。
- **根因**：三处口径不同；复制还把「原标题 + ' 副本'」截到 120，超过列宽 100。
- **影响**：MySQL 严格模式下 `DataError` → 通用 500「服务器开小差了」，用户不知道是标题太长；100 字标题复制必然失败。
- **修复**：schema 统一 `max_length=100`；复制改为 `[:100]` 并预留「 副本」长度；`create/update` 在 service 层再截断兜底。
- **验证**：创建 100 字标题的简历 → 复制 → 断言 200 且标题 ≤100。
- **状态**：已确认。

#### B-07 公告首次已读是 check-then-insert，并发会撞唯一约束返回 500（已确认，新增）

- **文件/函数**：`app/api/announcements.py:34-52`（`mark_announcement_read`）
- **触发**：同一用户同时打开两个标签页拉公告详情，或前端重试。
- **根因**：先 `select` 判断是否存在，再 `insert`；没有捕获 `IntegrityError`，也没有 `INSERT ... ON DUPLICATE KEY`。
- **影响**：其中一个请求 500；`read_count` 不会多加（事务回滚），但用户看到报错，且公告详情接口是进入工作区就会调用的。
- **修复**：捕获 `IntegrityError` 后当作「已读」继续；或改用 `INSERT IGNORE` / `ON DUPLICATE KEY UPDATE`。
- **验证**：并发 10 次 GET 同一公告，断言全部 200 且 `announcement_reads` 只有一行。
- **状态**：已确认（竞态窗口明确）。

#### B-08 版本创建的「先查后插」无锁，并发可产生重复快照；恢复的乐观锁存在 TOCTOU（已确认）

- **文件/函数**：`app/services/resume_service.py:196-228`（`create_version`）、`:310-365`（`restore_version`）
- **触发**：两个标签页同时手动存档；或恢复期间另一标签页保存。
- **根因**：`create_version` 读 `_latest_version` 比较 hash 后插入，没有 `(resume_id, content_hash)` 唯一约束，也没有行锁；`restore_version` 在检查 `expected_update_time` 之后调用 `create_version`（内部 commit），检查与写入之间可插入别的写。
- **影响**：重复版本行（列表出现两条一模一样的快照）；恢复可能覆盖检查之后刚落库的保存。
- **修复**：加 `(resume_id, content_hash)` 唯一索引并捕获冲突；恢复用 `SELECT ... FOR UPDATE` 锁简历行，或把「检查 + 写入」放进同一事务（`create_version` 不在中途 commit）。
- **验证**：并发 10 次 `POST /versions`，断言同 hash 只有一行；恢复与保存并发，断言 409。
- **状态**：已确认（机制）；并发概率**待测**。

#### B-09 热查询缺组合索引（已确认，新增）

- **文件/函数**：`app/models/ai_task.py:16-31`；`app/models/ai_config.py:55-65`；`app/models/resume.py:13-28`
- **触发**：AI 记录、积分流水、简历列表在数据量增长后。
- **根因**：`my_ai_records` 按 `user_id` 过滤 + `create_time desc` 排序；流水同理；简历列表按 `user_id` + `update_time desc`。当前只有单列索引，MySQL 需 filesort。
- **影响**：万级数据起列表明显变慢；后台按用户查询同样。
- **修复**：`ai_tasks(user_id, create_time)`、`flow_point_transactions(user_id, create_time)`、`resumes(user_id, update_time)`；用新迁移添加，避免与模型漂移。
- **验证**：造 10 万行后 `EXPLAIN` 看是否 `Using filesort`。
- **状态**：已确认（索引缺失）；实际耗时**待测**。

### P2

#### B-10 `/messages` 每次返回 80 条消息的全量 `optimized_resume_data`（已确认）

- **文件/函数**：`app/services/ai_chat_service.py:87-105`（`_message_out`）、`:362-375`（`list_chat_messages`）
- **影响**：一份 100KB 简历 × 多条 pending = 数 MB 响应；移动端加载慢、内存高。
- **修复**：列表投影掉 `optimized_resume_data`，只在 `GET /messages/{id}` 或 preview 时取；或加 `include_data` 参数。
- **状态**：已确认。

#### B-11 `/templates` 每次请求同步 DB 并渲染 12 份完整简历预览（已确认）

- **文件/函数**：`app/api/templates.py:12-22`；`app/services/template_service.py:206-212,240-273`
- **影响**：公开端点、无鉴权、无缓存（Redis 缓存只在 `db=None` 时使用），单次请求 12 次 Jinja 渲染 + 一次全表 SELECT。可被用来放大 CPU。
- **修复**：API 走缓存版本；`_with_preview` 结果按模板 id 缓存（模板内容变更时失效）；列表分页或按需渲染。
- **状态**：已确认。

#### B-12 信息库「上限检查 + 去重查询」都是先查后写（已确认）

- **文件/函数**：`app/services/info_fragment_service.py:233-283`（`_upsert`）、`:717-746`（`apply_proposals`）
- **影响**：并发可突破 `MAX_FRAGMENTS_PER_USER`；`fingerprint` 无唯一约束，并发可插入同内容两条；`apply_proposals` 逐条 commit，后续简历写入失败时留下半成功。
- **修复**：`(user_id, fingerprint)` 唯一索引；上限用计数表或 `SELECT ... FOR UPDATE`；提案应用合并为一个事务。
- **状态**：已确认。

#### B-13 看板把匹配行全量拉进 Python 计算活跃/时段指标（已确认）

- **文件/函数**：`app/api/admin/dashboard.py:235-241,624-645`
- **影响**：数据量增长后一次看板请求可加载数十万行，内存与网络开销大；管理端单点即影响 DB。
- **修复**：改成 SQL 聚合（`COUNT(DISTINCT ...)`、按小时 `GROUP BY`），或加时间窗与采样。
- **状态**：已确认。

#### B-14 分享脱敏只覆盖 `basics`（已确认）

- **文件/函数**：`app/services/resume_share_service.py:82-105`
- **影响**：用户勾选「隐藏敏感信息」后，写在自定义模块/个人简介里的手机号、邮箱仍会出现在公开页；默认 `share_mask_sensitive=False`，等于默认公开全部联系方式。
- **修复**：脱敏时对 `summary`、`custom_sections`、`basics.custom_fields` 做同样的字段级过滤；或在 UI 明确提示「只隐藏基本信息字段」。
- **状态**：已确认（产品口径问题）。

#### B-15 三个上传接口的「对象已写、DB 未写」无补偿（已确认）

- **文件/函数**：`app/services/storage/storage_service.py:178-196` 等
- **影响**：DB 失败留孤儿对象；响应丢失后重试产生重复对象；`uploaded_files.object_name` 无唯一索引，无法自动去重。
- **修复**：先插 pending 行、再写对象、成功置 success；或后台按「无 DB 引用」清理对象；给 `object_name` 加唯一索引。
- **状态**：已确认。

#### B-16 PDF 响应缺 `Cache-Control`（已确认）

- **文件/函数**：`app/api/exports.py:23-25`（`export_resume_pdf`）
- **影响**：`/preview-pdf` 已显式 `no-store`，但 `/export/pdf` 的 `FileResponse` 没有任何缓存头，浏览器/中间代理可能按启发式缓存已认证的 PDF；用户导出后修改简历，再打开可能拿到旧文件。
- **修复**：统一加 `Cache-Control: private, no-store`。
- **状态**：已确认（低概率，取决于代理）。

#### B-17 无 per-IP 登录限频，bcrypt 成本可被用来打满线程池（已确认）

- **文件/函数**：`app/api/auth.py:120-137`；`app/core/throttle.py:20-27`
- **触发**：攻击者用大量不同邮箱 + 同一 IP 尝试登录。
- **根因**：限频维度是 `邮箱|IP`，每个新邮箱都是新计数；bcrypt 默认 cost 使单次校验约 200ms。
- **影响**：40 线程池在 ~200 req/s 下饱和，登录与其他同步接口全部排队。
- **修复**：增加 per-IP 全局阈值（如 60 次/分钟）；失败后再做 bcrypt（先查用户存在性也不要跳过 timing 混淆）；必要时把 bcrypt 放线程池并限并发。
- **状态**：已确认（机制），实际攻击成本**待评估**。

#### B-18 同步 SMTP / OCR / Chromium 混在请求线程（已确认）

- **文件/函数**：`app/services/email_service.py:15-27`；`app/api/auth.py:78-120`（注册发信）；`app/api/feedback.py:56-70`
- **影响**：SMTP 10s 超时、OCR 无超时、Chromium 渲染 5–60s，都占用 anyio 线程池；与 B-02/B-04 叠加会放大排隊。
- **修复**：发信与 OCR 改后台任务/队列；至少给请求加整体 deadline。
- **状态**：已确认。

### P3

| 编号 | 问题 | 位置 | 说明 |
| --- | --- | --- | --- |
| B-19 | 后台用户/简历搜索未转义 LIKE 通配符、无关键字长度上限 | `app/api/admin/users.py:46,137` | 与 `resume_service._escape_like`、`ai_chat_service._search_like_pattern` 不一致；搜 `%` 命中全部 |
| B-20 | 自定义分享链接最短 3 位，且公开端点无限频 | `app/services/resume_share_service.py:25,58-70`；`app/api/shares.py:13` | 3 位自定义 token 可枚举；分享本就是公开页，风险有限，但应设最短 8 位 |
| B-21 | 反馈正文上限 500k 且无频率限制 | `app/api/feedback.py:27` | 存储型资源滥用 |
| B-22 | 创建/复制简历、提交反馈无幂等键 | `app/api/resumes.py:100-113`；`app/api/feedback.py:48` | 双击/重试产生重复记录 |
| B-23 | 手动版本不受 50 条保留策略约束 | `app/services/resume_service.py:231-253` | 用户可无限存档，每份都是全量 JSON；建议加总量或按用户配额 |
| B-24 | `/docs`、`/openapi.json` 默认公开 | `app/main.py:56` | 生产建议关闭或加鉴权 |
| B-25 | 邮箱枚举（重置密码明确返回「尚未注册」） | `app/api/auth.py:62-73` | 有 Redis 限频兜底，风险低 |
| B-26 | 管理员看板模板名/分类以静态清单为准，DB 行只承载可见性 | `app/services/template_service.py:240-273` | 我原本怀疑「后台改名被同步覆盖」，核实后后台只允许改 `sort_order/is_visible`，**不构成 bug** |

---

## 4. 安全审计

| 面向 | 结论 | 证据 |
| --- | --- | --- |
| SQL 注入 | **未发现** | 全仓无字符串拼接 SQL；`LIKE` 均用绑定参数；简历名与会话搜索做了 `%/_/\` 转义；后台两处搜索是例外（B-19） |
| XSS | **未发现可利用链路** | Jinja autoescape；`safe` 过滤器仅用于 bleach 后的富文本；图标宏不拼用户串；`ai_records_hint`/`user_agreement` 读写双侧净化（我专门验证过，**不成立**）；公告入库前净化 |
| SSRF | 主链路已防 | PDF 资产 `probe_public_url` + 三类白名单 + Chromium 全请求拦截；BYOK 仍是弱点（关联 RF-012） |
| CSRF | **不适用** | 全站 Bearer token，无 Cookie 会话 |
| IDOR | **未发现** | 简历/会话/消息/版本/附件/信息库/头像均按 `user_id` 收敛；删除越权返回 404 不泄露存在性 |
| 文件上传 | 类型/大小/前缀/归属齐全 | 但内存峰值（B-03）与对象/DB 一致性（B-15）是缺口 |
| 敏感信息泄露 | 两处 | JWT 入 URL（前端 RF-025）+ 弱 JWT 密钥（B-01）；错误 500 已隐藏内部细节 |
| 权限绕过 | **未发现** | 10 个 admin 模块统一依赖；资源校验在 service 层而非路由层，少一处漏一处 |
| 数据隔离 | 强 | 附件 `owned_object_names`、会话 `get_chat_session`、版本 `get_version` 均双重校验 |
| 日志隐私 | 较好 | `redact` 覆盖 Authorization/token 查询串；访问日志只记 `path` 不记 query |

---

## 5. 数据库与事务审计

### 5.1 事务边界

| 操作 | 边界 | 评价 |
| --- | --- | --- |
| 简历创建/更新/删除 | 单次 commit | 干净 |
| 版本创建 | service 内 commit | **在更大流程中提交**（B-08） |
| 版本恢复 | 先备份 commit → 写入 → 回读校验 → commit | 备份故意不随回滚，注释已说明；TOCTOU 见 B-08 |
| AI 确认 | 信息库逐条 commit → 简历写入 → 最终 commit | 部分成功（B-12，关联 RF-040） |
| 积分结算 | 条件 UPDATE + 流水同事务 | 原子性正确（`_apply_balance_delta`） |
| 兑换码 | 行锁 + 唯一约束 + 计数 | **正面样本** |
| 公告已读 | check-then-insert | 竞态（B-07） |

### 5.2 N+1 与查询形态

- 会话列表：一次聚合 + 一次取内容，**无 N+1**（`_chat_session_payloads` 的实现是对的）。
- 后台用户列表：`resume_count` 用子查询 join，**无 N+1**。
- AI 记录/流水：单查询 join，**无 N+1**。
- 看板：无 N+1，但全量拉取（B-13）。
- 模板列表：一次 SELECT + 12 次渲染（B-11）。

### 5.3 迁移与回滚

- 单头、无断链、id 长度合规。
- `0043` 的 downgrade 是空实现并写明理由（数据回填不可逆）——这是正确处理，不是缺陷。
- 抽查的列新增都带 `server_default` 或可空，老数据不会因 NOT NULL 失败。
- **风险点**：`0043` 的 `UPDATE` 会在启动迁移时锁全表（`resume_versions`）。当前数据量小可接受；若未来表大，应分批。P3。

---

## 6. 性能问题

| 级别 | 问题 | 机制 |
| --- | --- | --- |
| P1 | 连接池被长渲染占用（B-02） | 请求 Session 事务未结束 → 连接不归还 |
| P1 | 上传内存峰值（B-03） | `read()` 先于限额 |
| P1 | 导入同步 + 无上限（B-04） | PDF 逐页解析、OCR 4 轮、无超时 |
| P2 | 模板列表每请求 12 次渲染（B-11） | 公开端点、缓存被绕过 |
| P2 | 消息列表全量快照（B-10） | 无投影 |
| P2 | 看板全量载入（B-13） | Python 侧聚合 |
| P3 | 缺组合索引（B-09） | filesort |
| P3 | 同步 SMTP（B-18） | 10s 阻塞 |
| 正面 | PDF 指纹缓存、预览单飞（前端）、会话聚合、技能包限额 | — |

---

## 7. 异常处理问题

1. **`AppException` 默认 500**：语义上逼调用方显式传码，实际执行得不错；但 `announcement dismiss` 的 Redis 失败返回 500（`announcement_cache_service.py:87-88`），更合适是 503。P3。
2. **数据库 `DataError` 无专门处理**：B-06 的标题超长落到通用 500；建议把 `DataError/IntegrityError` 映射成 400/409 并保留日志。
3. **`_mark_import_task_failed` 掩盖原始异常**：导入失败统一 400「简历导入失败：...」，AI 侧 502 被降级成 400，调用方无法区分「模型失败」与「用户文件问题」。P3。
4. **流式接口的异常收尾**：`stream_ai_service_chat` 在 `except` 里 rollback 后 re-raise，外层 `iterator()` 再兜一层 error 事件——设计正确；但 `_ai_service_task_stream_response` 依赖 `finished` 标志，进程被杀时无收尾（关联 RF-009/RF-040）。
5. **`get_db` 的约束只写在注释里**：没有运行时断言阻止把请求级 Session 传进 `StreamingResponse`。建议加一个 `Session.info["streaming"]=False` 的 debug 断言或 lint 规则。

---

## 8. 缺失测试

后端 524 个用例覆盖已经很扎实（计费原子性、越权、PDF 资源、兑换、导入导出、确认决策都有）。仍缺：

| 缺口 | 为什么重要 | 建议用例 |
| --- | --- | --- |
| 连接池/长请求 | B-02 是可用性级风险 | 并发 20 个 `preview-pdf` 时断言其他接口不超时（可 mock 渲染为 sleep） |
| 上传超限 | B-03 | 伪造 100MB UploadFile，断言在任何读取前拒绝 |
| 导入上限与 OCR 超时 | B-04 | 页数/像素/超时三个边界 |
| 标题长度 | B-06 | 100 字标题创建 + 复制 |
| 公告并发已读 | B-07 | 10 并发断言无 500、单行 |
| 版本并发去重 | B-08 | 同 hash 并发创建 |
| 分享脱敏 | B-14 | 自定义模块含手机号时断言被清 |
| 迁移升级 | 全链路 | `alembic upgrade head` 在空库与「0042 快照」上各跑一次 |
| 管理员搜索转义 | B-19 | 关键字 `%` 不应命中全部 |

---

## 9. 修复建议（按投入产出排序）

### 立即（1–2 天）

1. **B-01**：生产环境无条件校验 JWT 密钥；`.env.example` 标注必填。
2. **B-03**：上传改用 `file.size` 预检 + 分块读取；nginx 之外再兜一层。
3. **B-02**：渲染前结束事务（`db.commit()`/短会话）；显式配置连接池。
4. **B-06**：标题长度三处对齐到 100。

### 本周

5. **B-05**：渲染函数增加 `base_url` 参数；PDF 用内网、预览/分享用公开地址或相对路径。
6. **B-08**：版本表加 `(resume_id, content_hash)` 唯一索引；恢复加行锁。
7. **B-04**：导入加页数/像素/超时上限，OCR 加 `timeout`。
8. **B-07**：公告已读改 `INSERT IGNORE`。

### 两周内

9. **B-09**：三个组合索引（单独迁移）。
10. **B-10/B-11/B-13**：消息投影、模板缓存、看板 SQL 聚合。
11. **B-15/B-17**：上传两阶段 + 唯一索引；per-IP 登录阈值。
12. **B-12**：信息库唯一约束 + 提案合并事务。

### 可选

13. B-14 脱敏范围、B-19~B-26 的一致性清理、`/docs` 关闭、迁移分批。

---

## 10. 已检查 / 未检查范围

### 已检查

| 范围 | 内容 |
| --- | --- |
| core | `config`、`database`、`security`、`exceptions`、`throttle`、`request_utils`、`redis`、`redis` 降级路径、`template_config`、`resume_schema`（清洗/规范化入口）、`crypto`（启动校验）、`redact` |
| api | `auth`、`resumes`、`exports`、`shares`、`files`、`avatars`、`announcements`、`feedback`、`templates`、`info_fragments`、`user_ai`（结构）、`admin/{users,points,settings,templates,records,dashboard,announcements,feedbacks,ai_configs,redeem_codes}` 的鉴权与关键查询 |
| models | 全部 11 个模型文件的表、列、索引、唯一约束 |
| schemas | `auth`、`resume`、`ai_chat`、`user_ai` 的长度/枚举/校验器 |
| services | `resume_service`（CRUD/版本/恢复）、`pdf_export_service`（指纹/锁/资产）、`preview_service`（渲染/字体/头像）、`storage_service`（上传/下载/头）、`info_fragment_service`（事务/去重）、`template_service`、`announcement_*`、`app_settings_service`、`email_service`、`resume_import_service`、`resume_json_export`、`resume_share_service`、`avatar_service`、`ai_task_service`、`flow_points_service`（结算/退款/兑换） |
| 迁移 | 43 个版本的链完整性、单头、id 长度、`0035/0042/0043` 的可重入与回填 |
| 测试 | 524 个用例的分布与关键用例名；针对 B-01~B-08 逐条核对是否已有覆盖 |
| 部署 | `requirements.txt` 锁定版本、`Dockerfile`、`docker-compose.yml`（端口/卷/健康检查）、`nginx.conf`、`.env.example` |

### 未检查 / 未执行

- 未跑任何测试、未启动服务、未做并发与故障注入；B-02/B-04/B-05 的实际影响需运行时确认。
- 未逐行审查：`admin/dashboard.py` 的每个指标算法（只看了查询形态与口径）、`resume_starter_*` 的内容生成、`word_export_service` 的 docx 细节（路由已下线）、`models/user.py` 之外的字段语义。
- 未验证：MySQL 版本/隔离级别、实际连接数、生产是否配了 `PDF_BASE_URL`、对象存储供应商差异（MinIO vs OSS 的 `Content-Type` 回放）。
- 未审计：依赖 CVE（只确认版本锁定策略合理）、镜像基础层、宿主机加固。

---

### 附：本轮推翻的两个假设（避免后继续误判）

1. **「后台 AI 提示文案可存储型 XSS」→ 不成立**：`ai_records_hint` 在 `RICH_TEXT_SETTINGS` 内，读、写两侧都过 `sanitize_rich_text_html`。
2. **「后台改模板名称会被内置同步覆盖」→ 不构成问题**：后台只允许改 `sort_order/is_visible`，被同步覆盖的 `name/category/is_pro` 本就不可编辑。
