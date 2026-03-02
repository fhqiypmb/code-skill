#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
System.md 自进化脚本
根据 agent 和 cold 记忆的积累，智能升级系统提示词
"""

import os
import re
from datetime import datetime

AGENT_DIR = ".opencode/agents/shuohan"
SYSTEM_FILE = f"{AGENT_DIR}/prompts/system.md"
BACKUP_DIR = f"{AGENT_DIR}/backup"
AGENT_MEMORY = f"{AGENT_DIR}/memory/agent.md"
COLD_MEMORY = f"{AGENT_DIR}/memory/cold.md"

def read_file(path):
    """读取文件"""
    if not os.path.exists(path):
        return ""
    with open(path, 'r', encoding='utf-8', errors='surrogateescape') as f:
        return f.read()

def write_file(path, content):
    """写入文件"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8', errors='surrogateescape') as f:
        f.write(content)

def get_version(content):
    """提取当前版本号"""
    match = re.search(r'v(\d+)\.(\d+)', content)
    if match:
        return int(match.group(1)), int(match.group(2))
    return 7, 0

def count_recent_entries(content, days=7):
    """统计最近 N 天的新增条目"""
    from datetime import datetime, timedelta
    cutoff = datetime.now() - timedelta(days=days)
    count = 0
    for line in content.split('\n'):
        if line.strip().startswith('- **'):
            try:
                date_str = line.split('**')[1].split(':')[0]
                entry_date = datetime.strptime(date_str, "%Y-%m-%d %H:%M")
                if entry_date > cutoff:
                    count += 1
            except:
                pass
    return count

def main():
    # 读取记忆文件
    system_content = read_file(SYSTEM_FILE)
    agent_content = read_file(AGENT_MEMORY)
    cold_content = read_file(COLD_MEMORY)

    # 统计最近新增
    agent_new = count_recent_entries(agent_content, days=7)
    cold_new = count_recent_entries(cold_content, days=7)

    print(f"Agent 记忆最近新增: {agent_new} 条")
    print(f"Cold 记忆最近新增: {cold_new} 条")

    # 判断是否需要进化
    should_evolve = False
    reason = []

    if agent_new >= 5:
        should_evolve = True
        reason.append(f"Agent 记忆新增 {agent_new} 条")

    if cold_new >= 3:
        should_evolve = True
        reason.append(f"Cold 记忆新增 {cold_new} 条")

    if not should_evolve:
        print("\n未达到进化条件")
        return

    print(f"\n触发进化条件: {', '.join(reason)}")
    print("\n=== 开始 System.md 自进化 ===")

    # 备份当前版本
    major, minor = get_version(system_content)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{BACKUP_DIR}/system_v{major}.{minor}_{timestamp}.md"
    write_file(backup_path, system_content)
    print(f"✓ 已备份当前版本: {backup_path}")

    # 提供分析材料给 LLM
    print("\n--- 当前 System.md ---")
    print(system_content)
    print("\n--- Agent 记忆 (最近 10 条) ---")
    agent_lines = [l for l in agent_content.split('\n') if l.strip().startswith('- **')]
    print('\n'.join(agent_lines[-10:]))
    print("\n--- Cold 记忆 (最近 10 条) ---")
    cold_lines = [l for l in cold_content.split('\n') if l.strip().startswith('- **')]
    print('\n'.join(cold_lines[-10:]))

    print("\n--- 进化要求 ---")
    print("""
请基于上述材料，生成新版本的 system.md：

1. 从 Agent 记忆中提取可固化的经验（如工作流程、技术规范）
2. 从 Cold 记忆中提取可固化的用户偏好（如默认行为、安全规则）
3. 保持原有结构，只增强内容
4. 版本号递增为 v{}.{}

请直接输出完整的新 system.md 内容，以 "===START===" 开始，"===END===" 结束。
""".format(major, minor + 1))

    # 等待 LLM 输入新版本
    print("\n请输入新版本 system.md:")
    lines = []
    started = False
    while True:
        line = input()
        if "===START===" in line:
            started = True
            continue
        if "===END===" in line:
            break
        if started:
            lines.append(line)

    new_system = '\n'.join(lines)

    # 写入新版本
    write_file(SYSTEM_FILE, new_system)
    print(f"\n✓ 已更新 system.md 到 v{major}.{minor + 1}")

    # 记录进化日志
    log_entry = f"\n- **{datetime.now().strftime('%Y-%m-%d %H:%M')}**: [进化] System.md 升级到 v{major}.{minor + 1}，原因: {', '.join(reason)}"
    agent_content += log_entry
    write_file(AGENT_MEMORY, agent_content)
    print("✓ 已记录进化日志到 Agent 记忆")

    print("\n=== 进化完成 ===")

if __name__ == "__main__":
    main()
