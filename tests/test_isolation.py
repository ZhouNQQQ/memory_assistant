"""与 Daimon 数据目录隔离的边界守卫测试（Requirements 6.1,6.2,6.4）。"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from kimiclaw_memory.config import (  # noqa: E402
    DEFAULT_DATA_DIR,
    DataDirBoundaryError,
    MemoryConfig,
    assert_safe_data_dir,
    to_core_dict,
)


class TestDataDirIsolation(unittest.TestCase):
    def test_default_is_independent_dir(self):
        self.assertIn(".kimiclaw_memory", DEFAULT_DATA_DIR)
        self.assertNotIn("kimi-desktop", DEFAULT_DATA_DIR)

    def test_safe_dir_passes(self):
        assert_safe_data_dir(tempfile.mkdtemp())  # 不抛异常

    def test_daimon_dir_rejected(self):
        bad = "~/Library/Application Support/kimi-desktop/daimon-share/daimon/x"
        with self.assertRaises(DataDirBoundaryError):
            assert_safe_data_dir(bad)

    def test_qclaw_dir_rejected(self):
        with self.assertRaises(DataDirBoundaryError):
            assert_safe_data_dir("~/.qclaw/workspace/mem")

    def test_to_core_dict_guards_daimon_path(self):
        cfg = MemoryConfig(
            llm_api_key="sk",
            data_dir="~/Library/Application Support/kimi-desktop/evil",
        )
        with self.assertRaises(DataDirBoundaryError):
            to_core_dict(cfg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
