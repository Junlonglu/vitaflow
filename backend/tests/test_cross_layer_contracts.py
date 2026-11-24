"""跨语言契约：同一个数字在 Python / TypeScript / Node / Nginx 里必须是同一个。

这些值没法 import 互相引用（三种语言 + 一个配置文件），所以漂移是**默认结局**：
标题长度曾经就是这样散成 100 / 120 / 120，复制一份长标题简历必然 500。

因此这里不检查「代码怎么写的」，而是**直接读源文件解析出数值**再断言相等。
代价是正则很脆（改写法就得改这里），收益是任何一边被单独改动都会立刻变红——
这正是这类契约唯一有用的形态。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core import contracts
from app.models.ai_chat import AiChatSession
from app.models.resume import Resume
from app.schemas.ai_chat import (
    MAX_CHAT_ATTACHMENTS,
    MAX_CHAT_CONTENT_LENGTH,
    MAX_CHAT_SESSION_TITLE_LENGTH,
)
from app.schemas.resume import ResumeCreate, ResumeDuplicateRequest, ResumeUpdate
from app.services.storage import storage_service

REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(relative: str) -> str:
    path = REPO_ROOT / relative
    assert path.exists(), f"契约来源文件不存在：{relative}"
    return path.read_text(encoding="utf-8")


def _int_match(pattern: str, source: str, *, what: str) -> int:
    match = re.search(pattern, source)
    assert match, f"没在源码里找到 {what}（正则：{pattern}）——改写法就同步改这条断言"
    return int(match.group(1).replace("_", ""))


# ---------------------------------------------------------------------------
# 简历标题：模型列宽 / Pydantic 上限 / 前端输入框
# ---------------------------------------------------------------------------


def test_title_limit_matches_model_column():
    assert Resume.__table__.c.title.type.length == contracts.RESUME_TITLE_MAX


@pytest.mark.parametrize("model", [ResumeCreate, ResumeUpdate, ResumeDuplicateRequest])
def test_title_limit_matches_schema(model):
    field = model.model_fields["title"]
    limits = [meta.max_length for meta in field.metadata if hasattr(meta, "max_length")]
    assert limits == [contracts.RESUME_TITLE_MAX]


def test_title_limit_matches_frontend_constant():
    value = _int_match(
        r"export const RESUME_TITLE_MAX = (\d+)",
        _read("frontend/src/types/resume.ts"),
        what="前端 RESUME_TITLE_MAX",
    )
    assert value == contracts.RESUME_TITLE_MAX


# ---------------------------------------------------------------------------
# 上传与对话附件
# ---------------------------------------------------------------------------


def test_upload_limit_has_one_source():
    assert storage_service.MAX_SIZE == contracts.UPLOAD_MAX_BYTES
    assert storage_service.MAX_SIZE_MB == contracts.UPLOAD_MAX_MB


def test_chat_limits_match_schema():
    assert MAX_CHAT_ATTACHMENTS == contracts.CHAT_ATTACHMENT_MAX
    assert MAX_CHAT_CONTENT_LENGTH == contracts.CHAT_CONTENT_MAX
    assert MAX_CHAT_SESSION_TITLE_LENGTH == contracts.CHAT_SESSION_TITLE_MAX


def test_chat_title_has_room_in_the_column():
    """schema 上限必须小于列宽：上面是「留出余量」，不是「刚好卡满」。"""
    assert MAX_CHAT_SESSION_TITLE_LENGTH < AiChatSession.__table__.c.title.type.length


def test_frontend_attachment_limits_match_backend():
    source = _read("frontend/src/types/chat.ts")
    count = _int_match(
        r"export const CHAT_ATTACHMENT_MAX = (\d+)", source, what="前端 CHAT_ATTACHMENT_MAX"
    )
    total_mb = _int_match(
        r"export const CHAT_ATTACHMENT_TOTAL_MAX_MB = (\d+)",
        source,
        what="前端 CHAT_ATTACHMENT_TOTAL_MAX_MB",
    )
    assert count == contracts.CHAT_ATTACHMENT_MAX
    assert total_mb == contracts.CHAT_ATTACHMENT_TOTAL_MAX_MB


def test_composer_does_not_redeclare_the_attachment_count():
    """输入区只能引用常量。再写一个字面量 9，就等于给漂移开了第二个入口。"""
    source = _read("frontend/src/components/ai/AiComposer.vue")
    assert "const MAX_ATTACHMENTS = CHAT_ATTACHMENT_MAX" in source
    assert not re.search(r"const MAX_ATTACHMENTS = \d+", source)


def test_chat_body_budget_is_consistent():
    """附图总量 base64 后装得进 Node 的上限，Node 的上限又严于 Nginx。"""
    assert contracts.chat_body_budget_problems() == []


# ---------------------------------------------------------------------------
# 超时链：Node / 后端代理 / Nginx
# ---------------------------------------------------------------------------


def test_node_turn_timeout_default_matches_contract():
    value = _int_match(
        r"process\.env\.AI_TURN_TIMEOUT_MS \|\| ([\d_]+)",
        _read("ai-service/src/config.ts"),
        what="Node 的 AI_TURN_TIMEOUT_MS 默认值",
    )
    assert value == contracts.AI_TURN_TIMEOUT_MS


def test_node_body_limit_matches_contract():
    mb = _int_match(
        r"process\.env\.AI_BODY_LIMIT_MB \|\| (\d+)",
        _read("ai-service/src/config.ts"),
        what="Node 的 AI_BODY_LIMIT_MB 默认值",
    )
    assert mb * 1024 * 1024 == contracts.NODE_BODY_MAX_BYTES


def test_node_uses_the_configured_body_limit():
    """server.ts 里不能留硬编码的 "20mb"：那正是 RF-044 的原始错配。"""
    source = _read("ai-service/src/server.ts")
    assert "express.json({ limit: config.bodyLimitBytes })" in source
    assert not re.search(r'express\.json\(\{\s*limit:\s*"\d+mb"\s*\}\)', source)


def test_nginx_timeouts_and_body_limit_match_contract():
    source = _read("frontend/nginx.conf")
    body_mb = _int_match(r"client_max_body_size (\d+)m;", source, what="Nginx client_max_body_size")
    read_seconds = _int_match(r"proxy_read_timeout (\d+)s;", source, what="Nginx proxy_read_timeout")

    assert body_mb * 1024 * 1024 == contracts.NGINX_CLIENT_BODY_MAX_BYTES
    assert read_seconds * 1000 == contracts.NGINX_PROXY_READ_TIMEOUT_MS
    # 内层必须更严：否则超额请求被 Nginx 用 HTML 413 挡掉，前端拿不到可读错误。
    assert contracts.NODE_BODY_MAX_BYTES <= contracts.NGINX_CLIENT_BODY_MAX_BYTES


def test_timeout_chain_is_monotonic():
    assert (
        contracts.timeout_chain_problems(
            contracts.AI_TURN_TIMEOUT_MS,
            contracts.AI_PROXY_READ_TIMEOUT_MS,
            contracts.NGINX_PROXY_READ_TIMEOUT_MS,
        )
        == []
    )


def test_compose_defaults_match_contract():
    source = _read("docker-compose.yml")
    turn_ms = _int_match(
        r"AI_TURN_TIMEOUT_MS: \$\{AI_TURN_TIMEOUT_MS:-(\d+)\}",
        source,
        what="compose 的 AI_TURN_TIMEOUT_MS 默认值",
    )
    body_mb = _int_match(
        r"AI_BODY_LIMIT_MB: \$\{AI_BODY_LIMIT_MB:-(\d+)\}",
        source,
        what="compose 的 AI_BODY_LIMIT_MB 默认值",
    )
    assert turn_ms == contracts.AI_TURN_TIMEOUT_MS
    assert body_mb * 1024 * 1024 == contracts.NODE_BODY_MAX_BYTES


# ---------------------------------------------------------------------------
# 连接池
# ---------------------------------------------------------------------------


def test_pool_defaults_have_one_source():
    """看**声明的默认值**，不看解析结果：``backend/.env`` 可能覆盖真实值，
    而这里要守的是「没人写第二套默认数」。"""
    from app.core.config import Settings

    assert Settings.model_fields["db_pool_size"].default == contracts.DB_POOL_SIZE
    assert Settings.model_fields["db_max_overflow"].default == contracts.DB_MAX_OVERFLOW
    assert (
        Settings.model_fields["db_pool_timeout"].default == contracts.DB_POOL_TIMEOUT_SECONDS
    )
    assert (
        Settings.model_fields["ai_proxy_read_timeout_ms"].default
        == contracts.AI_PROXY_READ_TIMEOUT_MS
    )


# ---------------------------------------------------------------------------
# 模板清单与技能清单：允许各写一份，但不允许写完不比对
# ---------------------------------------------------------------------------


def test_template_names_match_backend_registry():
    """前端 ``templateNames`` 是兜底展示名，后端 ``TEMPLATES`` 是注册表。

    两份清单无法互相 import（一份 JSON 接口、一份前端常量），所以这里比对
    id 集合与展示名：新增模板只改后端、前端兜底名缺失会立刻变红。
    """
    from app.services.template_service import TEMPLATES

    backend = {item["template_id"]: item["name"] for item in TEMPLATES}
    source = _read("frontend/src/utils/templateNames.ts")
    pairs = re.findall(r"^\s*([a-z_]+):\s*\"([^\"]+)\",", source, flags=re.MULTILINE)
    frontend = dict(pairs)

    assert set(frontend) == set(backend), "前端兜底名与后端注册表的模板 id 不一致"
    assert frontend == backend, "同一模板的展示名在两端不一致"

    # 第三份拷贝：模板选择面板里的下拉项。它不会因为接口慢而变，所以更容易
    # 长期不同步——正是「同一份元数据三处」里最隐蔽的那一处。
    # 只取 `const templates = [...]` 这一段：同一文件里还有对齐/字体等选项。
    panel = _read("frontend/src/components/editor/StyleConfigPanel.vue")
    block = re.search(r"const templates = \[(.*?)\n\]", panel, flags=re.DOTALL)
    assert block, "没找到 StyleConfigPanel 的 templates 数组"
    panel_pairs = dict(
        re.findall(r'\{\s*value: "([a-z_]+)",\s*label: "([^"]+)"\s*\}', block.group(1))
    )
    assert panel_pairs == backend, "模板面板的下拉名与后端注册表不一致"


def test_skill_names_match_ai_service():
    """``chatSkills.ts`` 是用户点得到的卡片，``skills.ts`` 是模型读到的清单。

    名字对不上时，用户点了「润色」而服务端注入的是别的技能——不会报错，
    只会静默地做错事。
    """
    backend_source = _read("ai-service/src/skills.ts")
    backend_pairs = re.findall(
        r'id: "([a-z-]+)",\s*\n\s*name: "([^"]+)"', backend_source
    )
    assert backend_pairs, "没解析出 ai-service 的技能清单，检查 skills.ts 的字段顺序"

    frontend_source = _read("frontend/src/components/ai/chatSkills.ts")
    frontend_names = re.findall(r'\{\s*name: "([^"]+)"', frontend_source)

    assert [name for _, name in backend_pairs] == frontend_names


# ---------------------------------------------------------------------------
# 部署面：端口暴露与运行身份
# ---------------------------------------------------------------------------


def test_ai_service_port_is_bound_to_loopback():
    """8300 是服务间回调口，没有任何浏览器会直连它。

    发布成 `0.0.0.0:8300` 等于把「服务令牌 + 明文 user_id」的接口放到宿主机与
    同网段可达处（RF-008）。这条断言比文档可靠：改回来立刻变红。
    """
    compose = _read("docker-compose.yml")
    match = re.search(r'-\s*"([^"]*8300[^"]*)"', compose)
    assert match, "没在 docker-compose.yml 里找到 8300 的端口映射"
    assert match.group(1).startswith("127.0.0.1:"), (
        f"8300 映射成 {match.group(1)}，应绑定 127.0.0.1"
    )


def test_containers_run_as_non_root():
    """两个镜像都必须有 USER 指令（RF-037）。

    镜像里带 Chromium 与 Tesseract，以 root 运行时一次 RCE 就直接是全机权限。
    """
    for path in ("backend/Dockerfile", "ai-service/Dockerfile"):
        source = _read(path)
        match = re.search(r"^USER\s+(\S+)", source, flags=re.MULTILINE)
        assert match, f"{path} 没有 USER 指令，容器会以 root 运行"
        assert match.group(1) != "root", f"{path} 显式指定了 USER root"
