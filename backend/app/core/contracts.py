"""跨层共享的硬上限与运行时常量。

同一个数值如果分别写在「模型列宽 / Pydantic 校验 / service 截断 / 前端输入框 /
Node 服务 / Nginx」里，迟早会漂移。标题就吃过这个亏：列宽 100、校验 120、
复制时截 120，于是复制一份长标题简历必然 500（Data too long）。

约定：**一个参数只有一个来源**。新增这类上限时先加到这里，再由各处引用；
跨语言的那几处（前端 TS、Node、Nginx）无法 import 本模块，由
``tests/test_cross_layer_contracts.py`` 直接读源文件比对，以及 ``startup_checks``
在启动时校验彼此的关系。

本模块保持**零依赖**（不 import settings、不 import 任何 app 模块）：
它是契约本身，配置项依赖它，反过来不行——否则一改配置就循环导入。
"""

# ---------------------------------------------------------------------------
# 简历
# ---------------------------------------------------------------------------

# 简历标题。``models/resume.py`` 的列宽、``schemas/resume.py`` 的 max_length、
# service 的截断、前端 ``types/resume.ts::RESUME_TITLE_MAX`` 全部引用它。
RESUME_TITLE_MAX = 100

# 客户端幂等键的长度上限，与 resumes.request_id / user_feedbacks.request_id
# 的列宽一致。前端用 crypto.randomUUID()（36 字符），留出余量。
REQUEST_ID_MAX = 64


# ---------------------------------------------------------------------------
# 上传
# ---------------------------------------------------------------------------

# 单个上传对象的上限（头像 / 公告图 / 对话图片共用）。
UPLOAD_MAX_BYTES = 5 * 1024 * 1024
UPLOAD_MAX_MB = UPLOAD_MAX_BYTES // (1024 * 1024)


# ---------------------------------------------------------------------------
# AI 对话
# ---------------------------------------------------------------------------

CHAT_CONTENT_MAX = 8000
CHAT_SESSION_TITLE_MAX = 60

# 一次消息最多附带的图片数。前端 ``AiComposer.vue::MAX_ATTACHMENTS`` 必须一致。
CHAT_ATTACHMENT_MAX = 9
# 单张对话图片的上限，与上传上限同源。
CHAT_IMAGE_MAX_BYTES = UPLOAD_MAX_BYTES

# 一次消息的**附图总量**上限（base64 之前）。这才是请求体的真实约束：
# 9 张 × 5MB = 45MB，base64 后约 60MB，任何一个正常配置的 body limit 都装不下。
# 不设总量，Node 那边就会在「用户消息已落库」之后才 413，从而留下无回复的回合。
CHAT_ATTACHMENT_TOTAL_MAX_BYTES = 20 * 1024 * 1024
CHAT_ATTACHMENT_TOTAL_MAX_MB = CHAT_ATTACHMENT_TOTAL_MAX_BYTES // (1024 * 1024)

# Node 侧 express.json 的 body 上限。必须容纳「附图总量 base64 + 文本 + JSON 包裹」。
# 关系由 ``startup_checks`` 校验，见 ``chat_body_budget_problems()``。
NODE_BODY_MAX_BYTES = 32 * 1024 * 1024

# Nginx 的 ``client_max_body_size``。**必须大于** Node 的上限：最内层先拒，
# 才能返回一条前端能展示的 JSON 错误；反过来就是 Nginx 的 HTML 413 页面，
# 用户只看到「请求失败」，而且此时用户消息可能已经落库（RF-044）。
NGINX_CLIENT_BODY_MAX_BYTES = 40 * 1024 * 1024


def base64_size(raw_bytes: int) -> int:
    """base64 编码后的字节数（4/3 膨胀，向上取整）。"""
    return (raw_bytes + 2) // 3 * 4


def chat_body_budget_problems(
    *,
    body_max_bytes: int = NODE_BODY_MAX_BYTES,
    total_max_bytes: int = CHAT_ATTACHMENT_TOTAL_MAX_BYTES,
    content_max_chars: int = CHAT_CONTENT_MAX,
    nginx_max_bytes: int = NGINX_CLIENT_BODY_MAX_BYTES,
) -> list[str]:
    """校验「附图 total + 文本」装得进 body 上限，且内层限制严于外层。

    三层各自的职责：
    * 附图总量 —— 业务预算，决定用户一次能发多少张；
    * Node body —— 必须装得下这个预算 base64 之后的结果；
    * Nginx client_max_body_size —— 必须比 Node 宽，好让 Node 的 JSON 错误先返回。
    """
    problems: list[str] = []
    needed = base64_size(total_max_bytes) + content_max_chars * 4 + 64 * 1024
    if needed > body_max_bytes:
        problems.append(
            f"对话请求体上限不够：附图 {total_max_bytes // (1024 * 1024)}MB base64 后约 "
            f"{base64_size(total_max_bytes) // (1024 * 1024)}MB，加上文本需要 {needed // (1024 * 1024)}MB，"
            f"但 Node body 上限只有 {body_max_bytes // (1024 * 1024)}MB。"
            "请调大 AI_BODY_LIMIT_MB 或调小 CHAT_ATTACHMENT_TOTAL_MAX_BYTES。"
        )
    if nginx_max_bytes < body_max_bytes:
        problems.append(
            f"Nginx client_max_body_size（{nginx_max_bytes // (1024 * 1024)}MB）小于 Node body 上限"
            f"（{body_max_bytes // (1024 * 1024)}MB）：超额请求会在最外层被 Nginx 用 HTML 413 挡掉，"
            "前端拿不到可展示的错误信息。请把 nginx.conf 的 client_max_body_size 调到更大。"
        )
    return problems


# ---------------------------------------------------------------------------
# 超时链：Node 总时长 ≤ 后端代理读超时 ≤ Nginx 读超时
# ---------------------------------------------------------------------------

# 一个 agent 回合的**总**时长（Node 侧主动中断）。这是链上最短的那个：
# 它必须最先到期，才能让 Node 自己发一条错误事件、把回合正常收尾；
# 如果代理先断，前端只会看到一个没有结束原因的断流。
AI_TURN_TIMEOUT_MS = 300_000
# 后端 → Node 的读超时。比上一档长一个余量，用于接收最后几个 delta 与收尾。
AI_PROXY_READ_TIMEOUT_MS = 330_000
# Nginx → 后端。再长一个余量，保证最外层的 504 永远由「更内层已经失败」触发。
NGINX_PROXY_READ_TIMEOUT_MS = 360_000
# 相邻两档之间的最小间隔。相等或过于接近会在边界上互相抢断。
TIMEOUT_MARGIN_MS = 30_000


def timeout_chain_problems(
    turn_ms: int,
    proxy_ms: int,
    nginx_ms: int,
    margin_ms: int = TIMEOUT_MARGIN_MS,
) -> list[str]:
    """校验超时链是否严格单调，返回人类可读的问题列表（空表示通过）。"""
    if turn_ms <= 0 or proxy_ms <= 0 or nginx_ms <= 0:
        return [
            "超时链里有非正数，检查 AI_TURN_TIMEOUT_MS / "
            "AI_PROXY_READ_TIMEOUT_MS / NGINX_PROXY_READ_TIMEOUT_MS"
        ]
    problems: list[str] = []
    if proxy_ms < turn_ms + margin_ms:
        problems.append(
            f"后端代理读超时（{proxy_ms}ms）没有比 Node 回合总时长（{turn_ms}ms）"
            f"多出 {margin_ms}ms，回合会被代理先掐断、前端看到无原因的断流"
        )
    if nginx_ms < proxy_ms + margin_ms:
        problems.append(
            f"Nginx 读超时（{nginx_ms}ms）没有比后端代理读超时（{proxy_ms}ms）"
            f"多出 {margin_ms}ms，最外层会先返回 504，掩盖内层真实错误"
        )
    return problems


# ---------------------------------------------------------------------------
# 私有对象的签名 URL
# ---------------------------------------------------------------------------

# 对话图片不能带 Authorization 头，只能靠 URL 上的短签名（RF-025）。6 小时够
# 一屏历史图片慢慢加载，又不足以变成长期凭据；过期后下一次拉消息会重新签。
FILE_URL_TTL_SECONDS = 6 * 3600


# ---------------------------------------------------------------------------
# 服务间回合票据
# ---------------------------------------------------------------------------

# 票据要在**整个回合**里有效：签发在回合开始前，最后一次使用是回合结束后的
# usage 回调（还可能重试几次）。所以它必须比回合总时长更长，但不能长到一张
# 泄漏的票据能在几个回合之后继续用——600s 是「两倍回合时长」，够宽也够短。
# 关系由 ``ticket_ttl_problems`` 校验，启动自检会在 TTL 短于回合时长时拒绝启动。
SERVICE_TICKET_TTL_SECONDS = 600


def ticket_ttl_problems(ttl_seconds: int, turn_ms: int) -> list[str]:
    """票据有效期必须覆盖一个完整回合（含收尾回调）。"""
    if ttl_seconds <= 0:
        return ["SERVICE_TICKET_TTL_SECONDS 必须为正数"]
    turn_seconds = max(1, turn_ms // 1000)
    if ttl_seconds < turn_seconds:
        return [
            f"服务票据有效期（{ttl_seconds}s）短于一个回合的总时长（{turn_seconds}s）："
            "长回合的收尾回调会因为票据过期被拒，表现为「对话成功但没扣点」"
        ]
    return []


# ---------------------------------------------------------------------------
# 数据库连接池
# ---------------------------------------------------------------------------

# 显式配置而不是吃 SQLAlchemy 默认值（5 + 10 / 30s）：默认值在「15 个并发导出
# 就把整站排队 30s 再 500」这个量级上会直接打满全站（RF-053）。
DB_POOL_SIZE = 10
DB_MAX_OVERFLOW = 20
# 拿不到连接就快速失败：等 30s 只会把请求堆在池外，诱发雪崩。
DB_POOL_TIMEOUT_SECONDS = 5


def normalize_request_id(value: str | None) -> str | None:
    """幂等键归一：去空白、超长截断、空值视为「没有」。

    不做格式校验——调用方是自家前端，格式没意义；这里只保证它不会撞列宽、
    也不会把空串写进唯一索引（空串与空串之间是**冲突**，NULL 才不冲突）。
    """
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    return cleaned[:REQUEST_ID_MAX]
