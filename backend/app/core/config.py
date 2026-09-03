from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "VitaFlow"
    app_cn_name: str = "简历流"
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_debug: bool = True

    log_level: str = "INFO"
    uvicorn_log_level: str = "INFO"
    sqlalchemy_log_level: str = "WARNING"
    log_access_enabled: bool = True
    log_file: str = ""
    log_file_max_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    log_file_backup_count: int = Field(default=5, ge=0)

    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    db_host: str = "127.0.0.1"
    db_port: int = 3306
    db_user: str = "root"
    db_password: str = "123456"
    db_name: str = "vitaflow"
    db_charset: str = "utf8mb4"

    redis_url: str = "redis://127.0.0.1:6379/0"
    redis_key_prefix: str = "vitaflow"
    redis_socket_timeout: int = Field(default=3, gt=0)
    announcement_cache_ttl_seconds: int = Field(default=600, gt=0)

    jwt_secret_key: str = "please_change_this_secret_key"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 1440
    admin_emails: str = ""

    smtp_host: str = ""
    smtp_port: int = 465
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""
    smtp_from_name: str = "VitaFlow"
    smtp_use_ssl: bool = True
    smtp_use_tls: bool = False
    smtp_timeout: int = 10
    email_code_expire_minutes: int = Field(default=10, gt=0)
    email_code_send_interval_seconds: int = Field(default=60, gt=0)
    email_code_register_lock_seconds: int = Field(default=30, gt=0)

    # Node「AI 服务」（pi-agent 旁路服务）：AI 对话与一次性任务（生成/导入）
    # 均代理到该服务，由 Node 侧承载 pi-agent 运行时。模型目录与 API Key
    # 由 AI 服务持有（ADR-0005），后端不再保存任何直连模型配置。
    ai_service_url: str = "http://127.0.0.1:8300"
    ai_service_token: str = ""
    # AI 服务上下文注入上限：从 MySQL 读取最近 N 条历史注入 prompt。
    ai_service_context_messages: int = Field(default=12, ge=1, le=40)
    ai_chat_change_timeout: int = Field(default=180, gt=0)
    ai_model_io_log_enabled: bool = True
    ai_model_io_log_path: str = "logs/ai_model_io.log"
    ai_model_io_log_max_chars: int = Field(default=200000, gt=0)

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
