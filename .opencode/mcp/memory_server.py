#!/usr/bin/env python3
"""
硕含Agent 记忆MCP服务器
提供记忆读写工具给opencode使用
"""

import sys
import json
from pathlib import Path
from datetime import datetime

MEMORY_DIR = Path(__file__).parent.parent / "agents" / "shuohan" / "memory"

def get_file(mem_type: str) -> Path:
    files = {
        "hot": MEMORY_DIR / "hot.md",
        "cold": MEMORY_DIR / "cold.md",
        "agent": MEMORY_DIR / "agent.md"
    }
    return files.get(mem_type)

def read_memory(mem_type: str) -> str:
    file = get_file(mem_type)
    if file and file.exists():
        return file.read_text(encoding="utf-8")
    return f"记忆文件不存在: {mem_type}"

def write_memory(mem_type: str, content: str) -> str:
    file = get_file(mem_type)
    if not file:
        return f"未知的记忆类型: {mem_type}"

    file.parent.mkdir(parents=True, exist_ok=True)

    if not file.exists():
        titles = {"hot": "# Hot Memory\n\n", "cold": "# Cold Memory\n\n", "agent": "# Agent Memory\n\n"}
        file.write_text(titles[mem_type], encoding="utf-8")

    timestamp = datetime.now().strftime("%H:%M:%S") if mem_type == "hot" else datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"- **{timestamp}**: {content}\n"

    with open(file, "a", encoding="utf-8") as f:
        f.write(entry)

    return f"已写入{mem_type}记忆"

def search_memory(query: str) -> str:
    results = []
    for mem_type in ["hot", "cold", "agent"]:
        file = get_file(mem_type)
        if file and file.exists():
            for line in file.read_text(encoding="utf-8").split("\n"):
                if query.lower() in line.lower() and line.strip():
                    results.append(f"[{mem_type}] {line}")
    return "\n".join(results[:10]) if results else f"未找到: {query}"

# MCP协议处理
def main():
    for line in sys.stdin:
        try:
            request = json.loads(line)
            method = request.get("method", "")
            params = request.get("params", {})

            if method == "tools/list":
                response = {
                    "tools": [
                        {"name": "memory_read", "description": "读取记忆", "inputSchema": {"type": "object", "properties": {"type": {"type": "string", "enum": ["hot", "cold", "agent"]}}, "required": ["type"]}},
                        {"name": "memory_write", "description": "写入记忆", "inputSchema": {"type": "object", "properties": {"type": {"type": "string", "enum": ["hot", "cold", "agent"]}, "content": {"type": "string"}}, "required": ["type", "content"]}},
                        {"name": "memory_search", "description": "搜索记忆", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}
                    ]
                }
            elif method == "tools/call":
                tool_name = params.get("name", "")
                args = params.get("arguments", {})
                if tool_name == "memory_read":
                    response = {"content": read_memory(args["type"])}
                elif tool_name == "memory_write":
                    response = {"content": write_memory(args["type"], args["content"])}
                elif tool_name == "memory_search":
                    response = {"content": search_memory(args["query"])}
                else:
                    response = {"error": f"未知工具: {tool_name}"}
            else:
                response = {}

            print(json.dumps({"id": request.get("id"), "result": response}))
            sys.stdout.flush()
        except Exception as e:
            print(json.dumps({"error": str(e)}))
            sys.stdout.flush()

if __name__ == "__main__":
    main()
