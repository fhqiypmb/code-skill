import io

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
import os

# 修复Windows终端UTF-8输出
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')

import io
from pathlib import Path
from datetime import datetime

#!/usr/bin/env python3
"""
硕含Agent 记忆管理脚本
用法:
  python memory_ops.py read <hot|cold|agent>
  python memory_ops.py write <hot|cold|agent> "内容"
  python memory_ops.py search "关键词"
"""

import sys
import os
from pathlib import Path
from datetime import datetime

# 记忆目录 - 从 .opencode/skills/shuohan/ 往上到 .opencode/agents/shuohan/memory
MEMORY_DIR = Path(__file__).parent.parent.parent / "agents" / "shuohan" / "memory"

def get_file(mem_type: str) -> Path:
    files = {
        "hot": MEMORY_DIR / "hot.md",
        "cold": MEMORY_DIR / "cold.md",
        "agent": MEMORY_DIR / "agent.md"
    }
    return files.get(mem_type)

def read_memory(mem_type: str):
    """读取记忆"""
    file = get_file(mem_type)
    if file and file.exists():
        content = file.read_text(encoding="utf-8", errors="surrogateescape")
        print(content)
    else:
        print(f"[错误] 记忆文件不存在: {file}")

def write_memory(mem_type: str, content: str):
    """写入记忆"""
    file = get_file(mem_type)
    if not file:
        print(f"[错误] 未知的记忆类型: {mem_type}")
        return

    file.parent.mkdir(parents=True, exist_ok=True)

    # 初始化文件
    if not file.exists():
        titles = {
            "hot": "# Hot Memory\n\n",
            "cold": "# Cold Memory\n\n",
            "agent": "# Agent Memory\n\n"
        }
        file.write_text(titles[mem_type], encoding="utf-8", errors="surrogateescape")

    # 格式化时间戳
    if mem_type == "hot":
        timestamp = datetime.now().strftime("%H:%M:%S")
    else:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    # 追加内容
    entry = f"- **{timestamp}**: {content}\n"

    with io.open(file, "a", encoding="utf-8", errors="surrogateescape") as f:
        f.write(entry)

    print(f"[成功] 已写入{mem_type}记忆: {content[:50]}...")

def search_memory(query: str):
    """搜索记忆"""
    results = []

    for mem_type in ["hot", "cold", "agent"]:
        file = get_file(mem_type)
        if file and file.exists():
            content = file.read_text(encoding="utf-8", errors="surrogateescape")
            for line in content.split("\n"):
                if query.lower() in line.lower() and line.strip():
                    results.append(f"[{mem_type}] {line}")

    if results:
        print("\n".join(results[:10]))
    else:
        print(f"[提示] 未找到包含 '{query}' 的记忆")

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1].lower()

    if cmd == "read" and len(sys.argv) >= 3:
        read_memory(sys.argv[2])
    elif cmd == "write" and len(sys.argv) >= 4:
        write_memory(sys.argv[2], sys.argv[3])
    elif cmd == "search" and len(sys.argv) >= 3:
        search_memory(sys.argv[2])
    else:
        print(__doc__)

if __name__ == "__main__":
    main()
