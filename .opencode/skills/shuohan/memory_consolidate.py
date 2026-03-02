#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hot 记忆自动整合脚本
当 hot.md 超过 100 条时，由 LLM 智能分析并整合到 cold 和 agent
"""

import os
import sys
from datetime import datetime

MEMORY_DIR = ".opencode/agents/shuohan/memory"
HOT_FILE = f"{MEMORY_DIR}/hot.md"
COLD_FILE = f"{MEMORY_DIR}/cold.md"
AGENT_FILE = f"{MEMORY_DIR}/agent.md"

def read_file(path):
    """读取文件内容"""
    if not os.path.exists(path):
        return ""
    with open(path, 'r', encoding='utf-8', errors='surrogateescape') as f:
        return f.read()

def write_file(path, content):
    """写入文件内容"""
    with open(path, 'w', encoding='utf-8', errors='surrogateescape') as f:
        f.write(content)

def count_entries(content):
    """统计记忆条数"""
    return len([line for line in content.split('\n') if line.strip().startswith('- **')])

def main():
    # 读取 hot 记忆
    hot_content = read_file(HOT_FILE)
    hot_count = count_entries(hot_content)

    print(f"Hot 记忆条数: {hot_count}")

    if hot_count < 100:
        print("未达到整合阈值 (100 条)")
        return

    print("\n=== 开始智能整合 ===")
    print("\n请 LLM 分析以下 Hot 记忆，并返回 JSON 格式的整合方案：")
    print("\n--- Hot 记忆内容 ---")
    print(hot_content)
    print("\n--- 分析要求 ---")
    print("""
请分析上述 Hot 记忆，返回 JSON 格式：
{
  "to_cold": ["用户偏好1", "用户偏好2"],
  "to_agent": ["技术经验1", "技术经验2"],
  "keep_recent": 20
}

分析标准：
1. to_cold: 提取用户明确表达的偏好、习惯、规则
2. to_agent: 提取有技术价值、可复用的经验
3. keep_recent: 保留最近 20 条记录

请直接输出 JSON，不要其他内容。
""")

    # 等待 LLM 输入整合方案
    print("\n请输入整合方案 JSON (输入 END 结束):")
    lines = []
    while True:
        line = input()
        if line.strip() == "END":
            break
        lines.append(line)

    import json
    plan = json.loads('\n'.join(lines))

    # 执行整合
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    # 写入 cold
    if plan.get('to_cold'):
        cold_content = read_file(COLD_FILE)
        for item in plan['to_cold']:
            cold_content += f"\n- **{timestamp}**: {item}"
        write_file(COLD_FILE, cold_content)
        print(f"✓ 已写入 {len(plan['to_cold'])} 条到 Cold 记忆")

    # 写入 agent
    if plan.get('to_agent'):
        agent_content = read_file(AGENT_FILE)
        for item in plan['to_agent']:
            agent_content += f"\n- **{timestamp}**: [整合] {item}"
        write_file(AGENT_FILE, agent_content)
        print(f"✓ 已写入 {len(plan['to_agent'])} 条到 Agent 记忆")

    # 清理 hot，保留最近 N 条
    hot_lines = hot_content.split('\n')
    entries = [line for line in hot_lines if line.strip().startswith('- **')]
    keep_count = plan.get('keep_recent', 20)
    kept_entries = entries[-keep_count:]

    new_hot = "# Hot Memory\n\n" + '\n'.join(kept_entries) + '\n'
    write_file(HOT_FILE, new_hot)
    print(f"✓ Hot 记忆已清理，保留最近 {keep_count} 条")

    print("\n=== 整合完成 ===")

if __name__ == "__main__":
    main()
