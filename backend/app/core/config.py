from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core import contracts


DEFAULT_JWT_SECRET_KEY = "please_change_this_secret_key"

# 非开发环境（生产/预发）集合：这些环境下不回显异常原文，也不允许打开调试模式。
PRODUCTION_ENVS = frozenset({"production", "prod", "staging"})


class Settings(BaseSettings):
    app_name: str = "ResumeFlow"
    app_cn_name: str = "简历流"
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    # 默认关闭。打开后 Starlette 会把 traceback 直接回给前端（连异常处理器都不
    # 经过），所以本地开发需在 .env 里显式写 APP_DEBUG=true；生产/预发即使写
    # 成 true 也不生效，见 debug_enabled。
    app_debug: bool = False

    log_level: str = "INFO"
    uvicorn_log_level: str = "INFO"
    sqlalchemy_log_level: str = "WARNING"
    log_access_enabled: bool = True
    log_file: str = ""
    log_file_max_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    log_file_backup_count: int = Field(default=5, ge=0)

    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # 反向代理网段：只有直连对端落在这些网段时，才采信 X-Forwarded-For 等代理头。
    # 默认覆盖回环与 Docker 私网——超出这个范围还直接采信，等于把限频与审计
    # 交给请求方自己填。若前置了 CDN，请把 CDN 回源网段加进来，并让最外层代理
    # 用 $remote_addr 覆盖 XFF（见 frontend/nginx.conf）。
    trusted_proxy_networks: str = (
        "127.0.0.0/8,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
    )

    db_host: str = "127.0.0.1"
    db_port: int = 3306
    db_user: str = "root"
    db_password: str = "123456"
    db_name: str = "vitaflow"
    db_charset: str = "utf8mb4"
    # 连接池显式配置。吃 SQLAlchemy 默认的 5+10/30s 时，15 个并发导出就能把
    # 全站接口排队 30s 再 500（RF-053）。默认值来自 core.contracts。
    db_pool_size: int = Field(default=contracts.DB_POOL_SIZE, ge=1)
    db_max_overflow: int = Field(default=contracts.DB_MAX_OVERFLOW, ge=0)
    db_pool_timeout: int = Field(default=contracts.DB_POOL_TIMEOUT_SECONDS, ge=1)

    redis_url: str = "redis://127.0.0.1:6379/0"
    redis_key_prefix: str = "vitaflow"
    redis_socket_timeout: int = Field(default=3, gt=0)
    announcement_cache_ttl_seconds: int = Field(default=600, gt=0)

    jwt_secret_key: str = DEFAULT_JWT_SECRET_KEY
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 1440
    admin_emails: str = ""

    # BYOK 密钥的加密口令，独立于 JWT：逗号分隔的 ``kid=secret`` 列表，
    # 第一项用于写入，其余仅用于解密历史密文（轮换时把新 key 放到最前）。
    # 例：BYOK_ENCRYPTION_KEYS=v2=新口令,v1=旧口令
    # 未配置时回退到 JWT_SECRET_KEY 派生的旧密钥，保证存量密文可读。
    byok_encryption_keys: str = ""

    smtp_host: str = ""
    smtp_port: int = 465
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""
    smtp_from_name: str = "ResumeFlow"
    smtp_use_ssl: bool = True
    smtp_use_tls: bool = False
    smtp_timeout: int = 10
    email_code_expire_minutes: int = Field(default=10, gt=0)
    email_code_send_interval_seconds: int = Field(default=60, gt=0)
    email_code_register_lock_seconds: int = Field(default=30, gt=0)
    # 验证码是 6 位数字，10 分钟内可穷举：同一邮箱同一用途连续试错到上限后
    # 直接作废已发验证码，必须重新获取。
    email_code_max_verify_attempts: int = Field(default=5, ge=1, le=20)

    # 登录失败限频。两个维度必须同时存在：只按「邮箱+IP」时，限频键里含
    # 攻击者可控的 email——固定 IP 换着邮箱试，每个邮箱都是新配额，bcrypt
    # 照样被打满；只按 IP 又会在 NAT 下误伤整栋楼。
    auth_login_max_attempts: int = Field(default=5, ge=1, le=50)
    auth_login_ip_max_attempts: int = Field(default=30, ge=1, le=500)
    auth_login_lock_seconds: int = Field(default=600, gt=0)

    # Node「AI 服务」（pi-agent 旁路服务）：AI 对话与一次性任务（生成/导入）
    # 均代理到该服务，由 Node 侧承载 pi-agent 运行时。模型目录与 API Key
    # 由 AI 服务持有（ADR-0005），后端不再保存任何直连模型配置。
    ai_service_url: str = "http://127.0.0.1:8300"
    ai_service_token: str = ""
    # 用户自带模型（BYOK）默认只允许公网接入点；接本机/内网网关时由部署方显式放开。
    byok_allow_private_network: bool = False
    byok_probe_timeout_seconds: float = Field(default=12.0, gt=0, le=60)
    # AI 服务上下文注入上限：从 MySQL 读取最近 N 条历史注入 prompt。
    ai_service_context_messages: int = Field(default=12, ge=1, le=40)
    ai_chat_change_timeout: int = Field(default=180, gt=0)
    # 超时链：Node 回合总时长 ≤ 后端代理读超时 ≤ Nginx 读超时。
    # 三档默认值都在 core.contracts 里，同一语义只写一次；相互关系由
    # startup_checks 在启动时校验（相等或颠倒都会在边界上互相抢断，RF-033）。
    ai_turn_timeout_ms: int = Field(default=contracts.AI_TURN_TIMEOUT_MS, gt=0)
    ai_proxy_read_timeout_ms: int = Field(default=contracts.AI_PROXY_READ_TIMEOUT_MS, gt=0)
    nginx_proxy_read_timeout_ms: int = Field(default=contracts.NGINX_PROXY_READ_TIMEOUT_MS, gt=0)
    ai_model_io_log_enabled: bool = True
    ai_model_io_log_path: str = "logs/ai_model_io.log"
    ai_model_io_log_max_chars: int = Field(default=200000, gt=0)

    # ---- 生命周期清扫（M3）----
    # 非终态必须有租约：任务建好后超过 lease 仍未结算，视为执行方已失联。
    ai_task_lease_seconds: int = Field(default=900, ge=60)
    # 待确认提案的存活时间：过期的提案不能再被「应用」，否则会覆盖之后的新结果。
    ai_pending_proposal_ttl_hours: int = Field(default=24, ge=1)
    # 用户消息超过这个时间还没有助手消息、也没有任务行，才算孤儿回合。
    # 必须明显大于「一个回合可能持续的最长时间」（Node 侧总超时 300s + 收尾），
    # 否则正在慢速生成的回合会被误判成中断、被补一条 failed 占位任务。
    ai_orphan_turn_grace_seconds: int = Field(default=600, ge=60)
    maintenance_sweep_enabled: bool = True
    maintenance_sweep_interval_seconds: int = Field(default=300, ge=30)
    # 存储侧孤儿对象的真实删除默认关闭：只统计不删。头像对象即便索引行已删
    # 也可能仍被分享简历引用，自动删会直接打断外部访客看到的页面。
    maintenance_orphan_delete_enabled: bool = False

    storage_provider: str = "minio"
    storage_public_url_mode: str = "proxy"

    minio_endpoint: str = "127.0.0.1:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "vitaflow"
    minio_secure: bool = False
    minio_public_url: str = "http://127.0.0.1:9000/vitaflow"

    aliyun_oss_endpoint: str = ""
    aliyun_oss_access_key_id: str = ""
    aliyun_oss_access_key_secret: str = ""
    aliyun_oss_bucket: str = ""
    aliyun_oss_public_url: str = ""
    aliyun_oss_secure: bool = True

    export_dir: str = "storage/exports"
    upload_dir: str = "storage/uploads"
    pdf_base_url: str = "http://127.0.0.1:8000"
    pdf_renderer: str = "chromium"
    pdf_chromium_executable_path: str = ""
    pdf_render_timeout_ms: int = 30000
    frontend_url: str = "http://localhost:5173"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() in PRODUCTION_ENVS

    @property
    def debug_enabled(self) -> bool:
        """调试模式的实际开关：生产/预发一律关闭。

        ``APP_DEBUG`` 只是「是否允许开」的配置项，真正能否打开由环境决定。
        原因是 FastAPI 的 ``debug=True`` 会挂在 Starlette 的 ServerErrorMiddleware
        上，未捕获异常直接返回 traceback HTML，``@app.exception_handler(Exception)``
        根本不会被调用——等于把调用栈和源码片段发给前端。
        """
        return self.app_debug and not self.is_production

    @property
    def database_url(self) -> str:
        return (
            f"mysql+pymysql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}?charset={self.db_charset}"
        )

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def admin_email_list(self) -> list[str]:
        return [item.strip().lower() for item in self.admin_emails.split(",") if item.strip()]

    @property
    def backend_root(self) -> Path:
        return Path(__file__).resolve().parents[2]

    @property
    def export_path(self) -> Path:
        path = Path(self.export_dir)
        return path if path.is_absolute() else self.backend_root / path

    @property
    def upload_path(self) -> Path:
        path = Path(self.upload_dir)
        return path if path.is_absolute() else self.backend_root / path


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
