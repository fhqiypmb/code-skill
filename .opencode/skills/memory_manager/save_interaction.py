#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
import os
from pathlib import Path
from datetime import datetime

# 修复Windows终端UTF-8输出
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')

# 记忆目录
MEMORY_DIR = Path(__file__).parent.parent.parent / "agents" / "shuohan" / "memory"

def write_hot(user_msg: str, agent_msg: str):
    """写入hot记忆"""
    file = MEMORY_DIR / "hot.md"
    file.parent.mkdir(parents=True, exist_ok=True)

    if not file.exists():
        file.write_text("# Hot Memory\n\n", encoding="utf-8")

    timestamp = datetime.now().strftime("%H:%M:%S")
    entry = f"- **{timestamp}**: [用户] {user_msg}\n- **{timestamp}**: [硕含] {agent_msg}\n"

    with open(file, "a", encoding="utf-8") as f:
        f.write(entry)

    print(f"✓ Hot记忆已保存")

def check_and_write_cold(user_msg: str, agent_msg: str):
    """检测并写入cold记忆"""
    keywords = ["喜欢", "偏好", "习惯", "总是", "通常", "项目", "使用"]
    combined = user_msg + agent_msg

    if any(kw in combined for kw in keywords):
        file = MEMORY_DIR / "cold.md"
        if not file.exists():
            file.write_text("# Cold Memory\n\n", encoding="utf-8")

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = f"- **{timestamp}**: {user_msg[:50]}... → {agent_msg[:50]}...\n"

        with open(file, "a", encoding="utf-8") as f:
            f.write(entry)

        print(f"✓ Cold记忆已保存")

def check_and_write_agent(agent_msg: str):
    """检测并写入agent记忆"""
    keywords = ["解决", "修复", "经验", "建议", "方案", "架构"]

    if any(kw in agent_msg for kw in keywords):
        file = MEMORY_DIR / "agent.md"
        if not file.exists():
            file.write_text("# Agent Memory\n\n", encoding="utf-8")

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = f"- **{timestamp}**: {agent_msg[:100]}...\n"

        with open(file, "a", encoding="utf-8") as f:
            f.write(entry)

        print(f"✓ Agent记忆已保存")

def main():
    if len(sys.argv) < 3:
        print("用法: python save_interaction.py '用户问题' 'Agent回答'")
        return

    user_msg = sys.argv[1]
    agent_msg = sys.argv[2]

    write_hot(user_msg, agent_msg)
    check_and_write_cold(user_msg, agent_msg)
    check_and_write_agent(agent_msg)

if __name__ == "__main__":
    main()
