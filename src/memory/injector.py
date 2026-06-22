# src/memory/injector.py
"""
QClaw USER.md / SOUL.md 注入器。

将 KimiClawMemory 中提取的记忆映射到 QClaw 的 markdown 文件格式。

设计原则：
- 增量更新：不覆盖整个文件，只更新相关字段
- 分类映射：记忆 category → markdown 分节
- 用户可控：关键变更（如 Name）需确认，避免自动覆盖
- 尊重格式：保留 USER.md 的原始模板结构和注释
"""

import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# 常量：QClaw 文件路径
# ──────────────────────────────────────────────────────────────────
DEFAULT_QCLAW_DIR = Path.home() / ".qclaw" / "workspace"
USER_MD_TEMPLATE = """# USER.md - About Your Human

_Learn about the person you're helping. Update this as you go._

- **Name:**
- **What to call them:**
- **Pronouns:** _(optional)_
- **Timezone:**
- **Notes:**

## Context

_(What do they care about? What projects are they working on? What annoys them? What makes them laugh? Build this over time.)_

---

The more you know, the better you can help. But remember — you're learning about a person, not building a dossier. Respect the difference.
"""


# ──────────────────────────────────────────────────────────────────
# 记忆分类 → 目标字段映射
# ──────────────────────────────────────────────────────────────────
CATEGORY_TO_FIELD = {
    "personal": ["name", "what_to_call_them", "pronouns", "timezone"],
    "preference": ["notes", "context"],
    "professional": ["context"],
    "plan": ["context"],
    "activity": ["context"],
    "health": ["notes", "context"],
    "misc": ["context"],
}

# 需要用户确认的关键字段（避免自动覆盖错误信息）
CONFIRMATION_REQUIRED_FIELDS = {"name", "what_to_call_them", "pronouns"}


# ──────────────────────────────────────────────────────────────────
# USER.md 解析与序列化
# ──────────────────────────────────────────────────────────────────
class UserMdParser:
    """
    解析和更新 USER.md 文件。

    格式：
        - **Name:** value
        - **What to call them:** value
        - **Pronouns:** _(optional)_ value
        - **Timezone:** value
        - **Notes:** value
        ## Context
        ...markdown content...
    """

    def __init__(self, content: str):
        self.content = content
        self.fields = self._parse_fields()
        self.context = self._parse_context()

    def _parse_fields(self) -> Dict[str, str]:
        """解析顶层的 - **Key:** value 字段。"""
        fields = {}
        # 匹配 markdown 粗体列表项：- **Key:** value
        # 注意：粗体标记 ** 在 Key 前后，即 **Key:**（:** 结束粗体）
        # [^\S\n]* 匹配空白（不含换行符），[^\n]*? 匹配值（不含换行符）
        pattern = re.compile(r'^- \*\*(.+?):\*\*[^\S\n]*([^\n]*?)$', re.MULTILINE)
        for match in pattern.finditer(self.content):
            key = match.group(1).strip().lower().replace(" ", "_")
            value = match.group(2).strip()
            # 去掉模板注释，如 _(optional)_
            value = re.sub(r'\_\(.+?\)\_', '', value).strip()
            fields[key] = value
        return fields

    def _parse_context(self) -> str:
        """解析 ## Context 下方的内容。"""
        match = re.search(
            r'## Context\s*\n\n?(.*?)(?=\n---\s*$|\Z)',
            self.content,
            re.DOTALL | re.IGNORECASE,
        )
        if match:
            return match.group(1).strip()
        return ""

    def set_field(self, key: str, value: str):
        """设置顶层字段值。"""
        self.fields[key] = value

    def append_context(self, text: str):
        """在 Context 段落追加内容（去重）。"""
        if text not in self.context:
            if self.context:
                self.context += f"\n\n{text}"
            else:
                self.context = text

    def to_markdown(self) -> str:
        """序列化回 markdown 字符串。"""
        lines = self.content.splitlines()
        result_lines = []
        in_context = False
        context_buffer = []
        written_keys = set()

        for line in lines:
            # 匹配字段行：- **Name:** value（粗体在 Key 前后）
            field_match = re.match(r'^(- \*\*)(.+?)(:\*\*[^\S\n]*)', line)
            if field_match:
                key = field_match.group(2).strip().lower().replace(" ", "_")
                written_keys.add(key)
                if key in self.fields and self.fields[key]:
                    # 直接构造新行，保留粗体格式
                    field_name = field_match.group(2).strip()
                    result_lines.append(f"- **{field_name}:** {self.fields[key]}")
                else:
                    result_lines.append(line)
                continue

            # 检测 Context 段落开始
            if re.match(r'^## Context\b', line, re.IGNORECASE):
                in_context = True
                result_lines.append(line)
                continue

            # 检测 Context 段落结束（--- 或文件结束）
            if in_context and line.strip() == "---":
                in_context = False
                # 先写入当前 context，再写入 ---
                if self.context:
                    result_lines.append("")
                    result_lines.append(self.context)
                result_lines.append(line)
                continue

            # 在 Context 段落内：缓存原内容，最后用 self.context 替换
            if in_context:
                context_buffer.append(line)
                continue

            result_lines.append(line)

        # 如果文件以 Context 结束（没有 ---）
        if in_context and self.context:
            result_lines.append("")
            result_lines.append(self.context)

        # 追加原始文件中不存在的字段（在 Notes 和 Context 之间）
        missing_fields = []
        for key, value in self.fields.items():
            if key not in written_keys and value:
                # 将 key 转换为标题形式
                field_name = key.replace("_", " ").title()
                missing_fields.append(f"- **{field_name}:** {value}")

        if missing_fields:
            # 找到插入位置：在 Notes 后面或 Context 前面
            inserted = False
            for i, line in enumerate(result_lines):
                if re.match(r'^## Context\b', line, re.IGNORECASE):
                    # 在 Context 前插入
                    for mf in missing_fields:
                        result_lines.insert(i, mf)
                        i += 1
                    inserted = True
                    break
            if not inserted:
                # 如果没有 Context 段落，追加到末尾
                result_lines.extend(missing_fields)

        return "\n".join(result_lines)


# ──────────────────────────────────────────────────────────────────
# 核心注入器
# ──────────────────────────────────────────────────────────────────
class QClawInjector:
    """
    将记忆注入 QClaw 的 markdown 文件。

    使用方式：
        injector = QClawInjector()
        injector.inject_memories(memories, user_id="user_001")
    """

    def __init__(self, workspace_dir: Optional[str] = None):
        self.workspace_dir = Path(workspace_dir) if workspace_dir else DEFAULT_QCLAW_DIR

    def _read_user_md(self) -> UserMdParser:
        """读取 USER.md，如果不存在则创建模板。"""
        user_md_path = self.workspace_dir / "USER.md"
        if user_md_path.exists():
            content = user_md_path.read_text(encoding="utf-8")
        else:
            content = USER_MD_TEMPLATE
            user_md_path.write_text(content, encoding="utf-8")
        return UserMdParser(content)

    def _write_user_md(self, parser: UserMdParser):
        """写回 USER.md。"""
        user_md_path = self.workspace_dir / "USER.md"
        user_md_path.write_text(parser.to_markdown(), encoding="utf-8")
        logger.info(f"Updated USER.md: {user_md_path}")

    def _is_safe_to_update(self, field: str, new_value: str, old_value: str) -> bool:
        """
        判断字段是否可以安全自动更新。

        - 空值 → 可以填充
        - 非确认字段 → 可以更新
        - 确认字段已有值 → 需要用户确认（暂不自动覆盖）
        """
        if not old_value or old_value.strip() in {"", "_(optional)_"}:
            return True
        if field not in CONFIRMATION_REQUIRED_FIELDS:
            return True
        return False

    def _extract_value(self, memory: dict, field: str) -> Optional[str]:
        """从记忆内容中提取特定字段的值（保留原始大小写）。"""
        data = memory.get("data", "")
        # 使用原始 data 匹配，re.IGNORECASE 实现不区分大小写

        if field == "name":
            # 匹配 "name is ..." / "我叫 ..." / "User's name is ..."
            patterns = [
                r"(?:name is|我叫|我的名字是|user's name is|我叫)\s*([\u4e00-\u9fa5A-Za-z\s]+?)(?:，|,|。|!|\.|$)",
                r"(?:name is|我叫)\s+([\u4e00-\u9fa5A-Za-z\s]+?)(?:，|,|。|!|\.|$)",
            ]
            for p in patterns:
                m = re.search(p, data, re.IGNORECASE)
                if m:
                    return m.group(1).strip()

        elif field == "what_to_call_them":
            # 匹配称呼
            patterns = [
                r"(?:call me|叫我|你可以叫我|叫我|你可以称呼我|称呼我)\s*([\u4e00-\u9fa5A-Za-z\s]+?)(?:，|,|。|!|\.|$)",
            ]
            for p in patterns:
                m = re.search(p, data, re.IGNORECASE)
                if m:
                    return m.group(1).strip()

        elif field == "timezone":
            patterns = [
                r"(?:timezone|时区|time zone)\s*(?:is|[:：])?\s*(\w+[/\w]+)",
                r"(?:in|住在|位于)\s*([A-Za-z\s]+?)\s*(?:timezone|time)",
            ]
            for p in patterns:
                m = re.search(p, data, re.IGNORECASE)
                if m:
                    return m.group(1).strip()

        return None

    # ──────────────────────────────
    # 公共 API
    # ──────────────────────────────
    def inject_memories(self, memories: List[dict], user_id: str = "default") -> Dict[str, int]:
        """
        将记忆注入 USER.md。

        Args:
            memories: 从 mem0 提取的记忆列表，每条包含 data, category, metadata 等
            user_id: 用户标识（用于日志）

        Returns:
            统计：{"fields_updated": N, "context_appended": M, "skipped": K}
        """
        parser = self._read_user_md()
        stats = {"fields_updated": 0, "context_appended": 0, "skipped": 0}

        context_items = []

        for mem in memories:
            data = mem.get("data", "")
            category = mem.get("category", "misc")
            target_fields = CATEGORY_TO_FIELD.get(category, ["context"])

            # 尝试提取结构化字段
            field_updated = False
            for field in target_fields:
                if field == "context":
                    continue

                extracted = self._extract_value(mem, field)
                if extracted:
                    old_value = parser.fields.get(field, "")
                    if self._is_safe_to_update(field, extracted, old_value):
                        parser.set_field(field, extracted)
                        stats["fields_updated"] += 1
                        field_updated = True
                        logger.info(f"USER.md field '{field}' updated: '{extracted}'")
                    else:
                        logger.debug(f"Skipped updating '{field}' (needs confirmation): '{extracted}'")

            # 未匹配到结构化字段的，放入 Context
            if not field_updated and data:
                context_items.append(data)

        # 去重后追加 Context
        existing_context = parser.context
        for item in context_items:
            if item not in existing_context:
                parser.append_context(item)
                stats["context_appended"] += 1

        self._write_user_md(parser)
        return stats

    def update_soul(self, preference_memories: List[dict]) -> Dict[str, int]:
        """
        将用户偏好类记忆注入 SOUL.md（用户的行为/偏好/边界）。

        例如：
        - "User prefers concise answers"
        - "User wants to be asked before any external action"
        → 追加到 SOUL.md 的 Continuity 或新增 Preferences 段落
        """
        soul_path = self.workspace_dir / "SOUL.md"
        if not soul_path.exists():
            logger.warning(f"SOUL.md not found at {soul_path}, skipping")
            return {"soul_updated": 0}

        content = soul_path.read_text(encoding="utf-8")
        stats = {"soul_updated": 0}

        # 查找或创建 "## Preferences" 段落
        pref_match = re.search(r'(## Preferences\s*\n\n?)(.*?)(?=\n## |\Z)', content, re.DOTALL)

        new_prefs = []
        for mem in preference_memories:
            data = mem.get("data", "")
            if data and data not in content:
                new_prefs.append(f"- {data}")

        if not new_prefs:
            return stats

        if pref_match:
            # 追加到现有 Preferences 段落
            existing = pref_match.group(2).strip()
            combined = existing + "\n" + "\n".join(new_prefs)
            content = content[:pref_match.start(2)] + combined + content[pref_match.end(2):]
        else:
            # 在 Continuity 之前插入 Preferences 段落
            continuity_match = re.search(r'(## Continuity\b)', content)
            if continuity_match:
                insert_pos = continuity_match.start()
                pref_block = "## Preferences\n\n" + "\n".join(new_prefs) + "\n\n"
                content = content[:insert_pos] + pref_block + content[insert_pos:]
            else:
                # 追加到文件末尾
                content += "\n\n## Preferences\n\n" + "\n".join(new_prefs) + "\n"

        soul_path.write_text(content, encoding="utf-8")
        stats["soul_updated"] = len(new_prefs)
        logger.info(f"Updated SOUL.md with {len(new_prefs)} preferences")
        return stats


# ──────────────────────────────────────────────────────────────────
# 便捷函数
# ──────────────────────────────────────────────────────────────────
def inject_to_qclaw(memories: List[dict], workspace_dir: Optional[str] = None) -> Dict[str, int]:
    """一键将记忆注入 QClaw。"""
    injector = QClawInjector(workspace_dir)
    return injector.inject_memories(memories)
