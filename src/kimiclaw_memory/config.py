"""配置加载层。

把分散的「默认值 / 可选 yaml / 环境变量(.env)」三级合并为 `MemoryConfig`，
并通过 `to_core_dict()` 组装成既有 `KimiClawMemory(config)` 所需的 dict
（mem0 原生 `llm`/`embedder`/`vector_store`/`history_db_path` + 自定义
`github_sync`/`compaction`/`qclaw` 段）。

约束：
- 密钥仅在运行时内存中传递，绝不写入任何持久化产物。
- 三级优先级：默认值 < yaml < 环境变量/.env。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional


class ConfigError(Exception):
    """配置非法（如缺少必要的 LLM 密钥）。"""


class DataDirBoundaryError(Exception):
    """数据目录越界：试图把本框架数据写入 Daimon 等受保护目录。"""


# 默认数据目录：与 Daimon 数据目录完全分离
DEFAULT_DATA_DIR = "~/.kimiclaw_memory"

# 受保护目录前缀：本框架绝不在这些目录下落盘（与 Daimon 原生记忆隔离）
_PROTECTED_DIR_FRAGMENTS = (
    "Library/Application Support/kimi-desktop",
    ".qclaw",
)


def assert_safe_data_dir(path: str) -> None:
    """确保数据目录不落在 Daimon / QClaw 等受保护目录内（Design Property 3）。"""
    resolved = str(Path(path).expanduser().resolve())
    for frag in _PROTECTED_DIR_FRAGMENTS:
        if frag in resolved:
            raise DataDirBoundaryError(
                f"数据目录 {resolved!r} 命中受保护路径片段 {frag!r}；"
                "本框架绝不在 Daimon/QClaw 目录下落盘，请改用独立目录。"
            )


@dataclass
class MemoryConfig:
    # ---- LLM（OpenAI 兼容：Kimi/Moonshot 或 智谱 GLM）----
    llm_provider: str = "openai"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.moonshot.cn/v1"
    llm_model: str = "moonshot-v1-8k"
    llm_temperature: float = 0.1
    # ---- Embedder（默认沿用 LLM 凭证；可单独覆盖）----
    embedder_provider: str = "openai"
    embedder_api_key: str = ""
    embedder_base_url: str = ""
    embedder_model: str = "text-embedding-3-small"
    embedding_dims: Optional[int] = None
    # ---- 向量库（Chroma 本地持久化）----
    data_dir: str = DEFAULT_DATA_DIR
    collection_name: str = "kimiclaw_memories"
    # ---- GitHub 同步（可选）----
    github_enabled: bool = False
    github_repo: str = ""
    github_token: str = ""
    github_branch: str = "main"
    github_sync_interval: int = 300
    github_batch_size: int = 20
    # ---- Compaction（可选）----
    compaction_enabled: bool = True
    half_life_days: float = 90.0
    dedup_threshold: float = 0.92
    archive_threshold: float = 0.3
    # ---- openclaw 遗留文件注入（可选，默认关）----
    enable_openclaw_inject: bool = False
    qclaw_workspace_dir: str = ""

    @property
    def vector_store_path(self) -> str:
        return str(Path(self.data_dir).expanduser() / "chroma")

    @property
    def history_db_path(self) -> str:
        return str(Path(self.data_dir).expanduser() / "history.db")


# ──────────────────────────────────────────────────────────────────
# 加载
# ──────────────────────────────────────────────────────────────────
def _read_yaml(path: str) -> Dict[str, Any]:
    import yaml  # 延迟导入

    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _env(name: str) -> Optional[str]:
    v = os.environ.get(name)
    return v if v not in (None, "") else None


def load_config(yaml_path: Optional[str] = None) -> MemoryConfig:
    """三级合并：默认值 < yaml（可选）< 环境变量/.env。

    Raises:
        ConfigError: 当 LLM 密钥（KIMI_API_KEY 与 ZHIPU_API_KEY）均缺失时。
    """
    cfg = MemoryConfig()

    # 2) yaml 覆盖（可选）
    if yaml_path and Path(yaml_path).expanduser().exists():
        data = _read_yaml(str(Path(yaml_path).expanduser()))
        for key, value in data.items():
            if hasattr(cfg, key):
                setattr(cfg, key, value)

    # 3) 环境变量 / .env 优先级最高
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:  # python-dotenv 不可用时静默跳过
        pass

    # LLM：优先 Kimi，兜底智谱 GLM
    cfg.llm_api_key = _env("KIMI_API_KEY") or _env("ZHIPU_API_KEY") or cfg.llm_api_key
    if _env("KIMI_API_KEY"):
        cfg.llm_base_url = _env("KIMI_BASE_URL") or cfg.llm_base_url
        cfg.llm_model = _env("KIMI_MODEL") or cfg.llm_model
    elif _env("ZHIPU_API_KEY"):
        cfg.llm_base_url = _env("ZHIPU_BASE_URL") or "https://open.bigmodel.cn/api/paas/v4"
        cfg.llm_model = _env("ZHIPU_MODEL") or "glm-4-flash"

    # Embedder：默认沿用 LLM 凭证，可被 EMBEDDING_* 覆盖
    cfg.embedder_api_key = _env("EMBEDDING_API_KEY") or cfg.embedder_api_key or cfg.llm_api_key
    cfg.embedder_base_url = _env("EMBEDDING_BASE_URL") or cfg.embedder_base_url or cfg.llm_base_url
    cfg.embedder_model = _env("EMBEDDING_MODEL") or cfg.embedder_model
    if _env("EMBEDDING_DIMS"):
        cfg.embedding_dims = int(_env("EMBEDDING_DIMS"))  # type: ignore[arg-type]

    # GitHub 同步
    cfg.github_repo = _env("GITHUB_REPO") or cfg.github_repo
    cfg.github_token = _env("GITHUB_TOKEN") or cfg.github_token
    cfg.github_enabled = bool(cfg.github_repo and cfg.github_token)

    # 数据目录
    cfg.data_dir = _env("KIMICLAW_DATA_DIR") or cfg.data_dir

    # openclaw 遗留注入
    if _env("ENABLE_OPENCLAW_INJECT"):
        cfg.enable_openclaw_inject = _env("ENABLE_OPENCLAW_INJECT").lower() in {"1", "true", "yes"}  # type: ignore[union-attr]
    cfg.qclaw_workspace_dir = _env("QCLAW_WORKSPACE_DIR") or cfg.qclaw_workspace_dir

    # 校验：LLM 密钥必须存在
    if not cfg.llm_api_key:
        raise ConfigError(
            "缺少 LLM 密钥：请设置环境变量 KIMI_API_KEY 或 ZHIPU_API_KEY（可写入 .env）。"
        )

    return cfg


# ──────────────────────────────────────────────────────────────────
# 组装为核心 KimiClawMemory 所需 dict
# ──────────────────────────────────────────────────────────────────
def to_core_dict(cfg: MemoryConfig) -> Dict[str, Any]:
    """转换为 `KimiClawMemory(config)` 所需的 dict。

    注意：密钥仅放入运行时 dict，传给 mem0 在内存中使用，不落盘。
    """
    # 边界守卫：绝不在 Daimon/QClaw 受保护目录下落盘
    assert_safe_data_dir(cfg.data_dir)
    # 确保数据目录存在
    Path(cfg.data_dir).expanduser().mkdir(parents=True, exist_ok=True)

    llm_config: Dict[str, Any] = {
        "model": cfg.llm_model,
        "api_key": cfg.llm_api_key,
        "openai_base_url": cfg.llm_base_url,
        "temperature": cfg.llm_temperature,
    }
    embedder_config: Dict[str, Any] = {
        "model": cfg.embedder_model,
        "api_key": cfg.embedder_api_key or cfg.llm_api_key,
        "openai_base_url": cfg.embedder_base_url or cfg.llm_base_url,
    }
    if cfg.embedding_dims is not None:
        embedder_config["embedding_dims"] = cfg.embedding_dims

    core: Dict[str, Any] = {
        "llm": {"provider": cfg.llm_provider, "config": llm_config},
        "embedder": {"provider": cfg.embedder_provider, "config": embedder_config},
        "vector_store": {
            "provider": "chroma",
            "config": {
                "collection_name": cfg.collection_name,
                "path": cfg.vector_store_path,
            },
        },
        "history_db_path": cfg.history_db_path,
    }

    if cfg.github_enabled:
        core["github_sync"] = {
            "enabled": True,
            "repo": cfg.github_repo,
            "token": cfg.github_token,
            "branch": cfg.github_branch,
            "sync_interval": cfg.github_sync_interval,
            "batch_size": cfg.github_batch_size,
        }

    if cfg.compaction_enabled:
        core["compaction"] = {
            "half_life_days": cfg.half_life_days,
            "similarity_threshold": cfg.dedup_threshold,
            "archive_threshold": cfg.archive_threshold,
        }

    if cfg.enable_openclaw_inject:
        core["qclaw"] = {
            "enabled": True,
            "workspace_dir": cfg.qclaw_workspace_dir or None,
        }

    return core
