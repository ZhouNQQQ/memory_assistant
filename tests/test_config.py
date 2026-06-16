"""配置层单元测试（Requirements 3.1~3.4）。"""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from kimiclaw_memory.config import (  # noqa: E402
    ConfigError,
    MemoryConfig,
    load_config,
    to_core_dict,
)

# 需要清理的环境变量，避免相互污染
_ENV_KEYS = [
    "KIMI_API_KEY", "KIMI_BASE_URL", "KIMI_MODEL",
    "ZHIPU_API_KEY", "ZHIPU_BASE_URL", "ZHIPU_MODEL",
    "EMBEDDING_API_KEY", "EMBEDDING_BASE_URL", "EMBEDDING_MODEL", "EMBEDDING_DIMS",
    "GITHUB_REPO", "GITHUB_TOKEN", "KIMICLAW_DATA_DIR",
    "ENABLE_OPENCLAW_INJECT", "QCLAW_WORKSPACE_DIR",
]


class ConfigTestBase(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in _ENV_KEYS}
        for k in _ENV_KEYS:
            os.environ.pop(k, None)
        # 隔离真实 .env：把 dotenv.load_dotenv patch 成 no-op，
        # 使测试只受显式设置的环境变量影响。
        self._dotenv_patch = mock.patch("dotenv.load_dotenv", lambda *a, **k: False)
        self._dotenv_patch.start()

    def tearDown(self):
        self._dotenv_patch.stop()
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TestLoadConfig(ConfigTestBase):
    def test_missing_llm_key_raises(self):
        with self.assertRaises(ConfigError):
            load_config()

    def test_kimi_key_sets_base_and_model(self):
        os.environ["KIMI_API_KEY"] = "sk-kimi-test"
        cfg = load_config()
        self.assertEqual(cfg.llm_api_key, "sk-kimi-test")
        self.assertIn("moonshot", cfg.llm_base_url)
        self.assertEqual(cfg.llm_model, "moonshot-v1-8k")

    def test_zhipu_fallback(self):
        os.environ["ZHIPU_API_KEY"] = "glm-test"
        cfg = load_config()
        self.assertEqual(cfg.llm_api_key, "glm-test")
        self.assertIn("bigmodel", cfg.llm_base_url)
        self.assertEqual(cfg.llm_model, "glm-4-flash")

    def test_kimi_takes_precedence_over_zhipu(self):
        os.environ["KIMI_API_KEY"] = "sk-kimi"
        os.environ["ZHIPU_API_KEY"] = "glm"
        cfg = load_config()
        self.assertEqual(cfg.llm_api_key, "sk-kimi")

    def test_env_overrides_kimi_base_model(self):
        os.environ["KIMI_API_KEY"] = "sk-kimi"
        os.environ["KIMI_BASE_URL"] = "https://custom/v1"
        os.environ["KIMI_MODEL"] = "moonshot-v1-32k"
        cfg = load_config()
        self.assertEqual(cfg.llm_base_url, "https://custom/v1")
        self.assertEqual(cfg.llm_model, "moonshot-v1-32k")

    def test_github_enabled_only_when_repo_and_token(self):
        os.environ["KIMI_API_KEY"] = "sk-kimi"
        cfg = load_config()
        self.assertFalse(cfg.github_enabled)  # 无 repo/token
        os.environ["GITHUB_REPO"] = "user/repo"
        os.environ["GITHUB_TOKEN"] = "ghp_x"
        cfg2 = load_config()
        self.assertTrue(cfg2.github_enabled)

    def test_embedder_falls_back_to_llm_creds(self):
        os.environ["KIMI_API_KEY"] = "sk-kimi"
        cfg = load_config()
        core = to_core_dict(cfg)
        self.assertEqual(core["embedder"]["config"]["api_key"], "sk-kimi")
        self.assertEqual(core["embedder"]["config"]["openai_base_url"], cfg.llm_base_url)

    def test_yaml_then_env_priority(self):
        import tempfile
        yaml_path = Path(tempfile.mkdtemp()) / "memory.yaml"
        yaml_path.write_text("llm_model: from-yaml\ncollection_name: yaml_coll\n", encoding="utf-8")
        os.environ["KIMI_API_KEY"] = "sk-kimi"
        # 未设 KIMI_MODEL → yaml 的 llm_model 生效
        cfg = load_config(str(yaml_path))
        self.assertEqual(cfg.collection_name, "yaml_coll")
        # 注意：设了 KIMI_API_KEY 分支会把 llm_model 设为 KIMI_MODEL or 默认；
        # 这里 KIMI_MODEL 未设，分支用 cfg.llm_model（即 yaml 值）兜底
        self.assertEqual(cfg.llm_model, "from-yaml")
        # env 覆盖 yaml
        os.environ["KIMI_MODEL"] = "from-env"
        cfg2 = load_config(str(yaml_path))
        self.assertEqual(cfg2.llm_model, "from-env")


class TestToCoreDict(ConfigTestBase):
    def test_core_dict_shape(self):
        os.environ["KIMI_API_KEY"] = "sk-kimi"
        cfg = load_config()
        core = to_core_dict(cfg)
        self.assertEqual(core["llm"]["provider"], "openai")
        self.assertEqual(core["llm"]["config"]["openai_base_url"], cfg.llm_base_url)
        self.assertEqual(core["vector_store"]["provider"], "chroma")
        self.assertIn("path", core["vector_store"]["config"])
        self.assertIn("history_db_path", core)
        # compaction 默认开启
        self.assertIn("compaction", core)
        # github 默认关闭 → 不应有 github_sync 段
        self.assertNotIn("github_sync", core)
        # 未启用 openclaw → 不应有 qclaw 段
        self.assertNotIn("qclaw", core)

    def test_no_secret_in_persistent_paths(self):
        os.environ["KIMI_API_KEY"] = "sk-secret"
        cfg = load_config()
        core = to_core_dict(cfg)
        # 密钥只应出现在内存 dict 的 config 中，不应混入路径
        self.assertNotIn("sk-secret", core["vector_store"]["config"]["path"])
        self.assertNotIn("sk-secret", core["history_db_path"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
