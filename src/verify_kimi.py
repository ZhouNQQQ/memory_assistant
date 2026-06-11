"""Task 0.1 验收脚本：验证 Kimi(Moonshot) key 是否可用。

用法：
  1. cp .env.example .env  并填入真实 KIMI_API_KEY
  2. .venv/bin/python src/verify_kimi.py
通过标准：打印 "✅ KIMI OK" 并附一句模型回复。
若失败，按提示换智谱兜底（见 .env.example 注释）。
"""
import os
import sys

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


def main() -> int:
    api_key = os.getenv("KIMI_API_KEY")
    base_url = os.getenv("KIMI_BASE_URL", "https://api.moonshot.cn/v1")
    model = os.getenv("KIMI_MODEL", "moonshot-v1-8k")

    if not api_key or api_key.startswith("sk-xxxx"):
        print("❌ 未配置 KIMI_API_KEY。请 cp .env.example .env 后填入真实 key。")
        return 1

    client = OpenAI(api_key=api_key, base_url=base_url)
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "用一句话回复：ping"}],
            temperature=0.0,
        )
    except Exception as e:  # noqa: BLE001 - 验收脚本，需暴露原始错误
        print(f"❌ 调用失败：{type(e).__name__}: {e}")
        print("   若为 401/额度问题，按 .env.example 注释切换智谱 GLM-4-Flash。")
        return 2

    content = resp.choices[0].message.content
    usage = resp.usage
    print("✅ KIMI OK")
    print(f"   model={model}  reply={content!r}")
    if usage:
        print(f"   tokens: prompt={usage.prompt_tokens} completion={usage.completion_tokens}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
