#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
硕含Agent 记忆管理脚本 - V2.0 整合版
支持双检查点防呆机制

用法:
  # 读取记忆
  python memory_ops.py read <hot|cold|agent>

  # 直接写入记忆（cold/agent 用此方法）
  python memory_ops.py write <hot|cold|agent> "内容"

  # Hot记忆双检查点（防呆机制）
  python memory_ops.py auto pre "计划做什么"     # 回复前
  python memory_ops.py auto post "实际完成"      # 回复后
  python memory_ops.py auto check                 # 检查是否有遗漏

  # 搜索记忆
  python memory_ops.py search "关键词"
"""

import sys
import os
import subprocess
from pathlib import Path
from datetime import datetime
import io

# 修复Windows终端UTF-8输出
if sys.platform == "win32":
    import codecs

    sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, "strict")
    sys.stderr = codecs.getwriter("utf-8")(sys.stderr.buffer, "strict")

# 记忆目录
MEMORY_DIR = Path(__file__).parent.parent.parent / "agents" / "shuohan" / "memory"
STATE_FILE = Path(__file__).parent / ".memory_state"


def get_file(mem_type: str) -> Path:
    files = {
        "hot": MEMORY_DIR / "hot.md",
        "cold": MEMORY_DIR / "cold.md",
        "agent": MEMORY_DIR / "agent.md",
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
    """直接写入记忆（用于cold/agent）"""
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
            "agent": "# Agent Memory\n\n",
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


# ========== 双检查点防呆机制 ==========


def auto_pre(content: str):
    """
    检查点1：回复前预写入
    创建状态标记，表示有待完成的记忆
    """
    timestamp = datetime.now().strftime("%H:%M:%S")
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        f.write(f"{timestamp}|{content}")

    print(f"📋 [记忆预写入] {timestamp}: {content[:50]}...")


def auto_post(content: str):
    """
    检查点2：回复后完成写入
    实际写入hot.md并清除状态标记
    """
    # 读取预写入状态
    pre_content = None
    pre_timestamp = None
    if STATE_FILE.exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = f.read()
        STATE_FILE.unlink()  # 删除状态文件
        if "|" in state:
            pre_timestamp, pre_content = state.split("|", 1)

    # 组合内容 - 新格式：[用户] 计划中内容 | [硕含] 实际内容
    if pre_content:
        full_content = f"[用户] {pre_content} | [硕含] {content}"
    else:
        full_content = f"[硕含] {content}"
        pre_timestamp = datetime.now().strftime("%H:%M:%S")

    # 写入hot记忆
    write_memory("hot", full_content)


def auto_check():
    """
    检查是否有未完成的记忆写入
    返回0表示正常，1表示有遗漏
    """
    if STATE_FILE.exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            content = f.read()
        if "|" in content:
            timestamp, pre = content.split("|", 1)
            print(f"⚠️  警告：发现未完成的记忆写入！")
            print(f"   时间: {timestamp}")
            print(f"   内容: {pre}")
            print(f"   请立即执行: python memory_ops.py auto post '实际完成的内容'")
        return 1
    else:
        print("✅ 检查通过：没有未完成的记忆写入")
        return 0


# ========== 搜索功能 ==========


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


# ========== 主入口 ==========


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1].lower()

    if cmd == "read" and len(sys.argv) >= 3:
        read_memory(sys.argv[2])

    elif cmd == "write" and len(sys.argv) >= 4:
        write_memory(sys.argv[2], sys.argv[3])

    elif cmd == "auto" and len(sys.argv) >= 3:
        subcmd = sys.argv[2].lower()
        if subcmd == "pre" and len(sys.argv) >= 4:
            auto_pre(sys.argv[3])
        elif subcmd == "post" and len(sys.argv) >= 4:
            auto_post(sys.argv[3])
        elif subcmd == "check":
            sys.exit(auto_check())
        else:
            print("[错误] auto 子命令用法: pre '内容' | post '内容' | check")

    elif cmd == "search" and len(sys.argv) >= 3:
        search_memory(sys.argv[2])

    else:
        print(__doc__)


if __name__ == "__main__":
    main()
