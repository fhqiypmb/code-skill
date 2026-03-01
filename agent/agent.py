#!/usr/bin/env python3
"""
硕含 Agent - Mem0 Style
- hot.md: 会话级记忆（当前会话）
- cold.md: 用户级记忆（跨会话，LLM提取）
- agent.md: Agent级记忆（Agent经验）
"""
"""

import os
- hot.md: 会话级记忆（当前会话）
- cold.md: 用户级记忆（跨会话，LLM提取）
- agent.md: Agent级记忆（Agent经验）
"""

import os
import sys
import yaml
import re
import atexit
from pathlib import Path
from datetime import datetime
from typing import List


def safe_text(text: str) -> str:
    return text.encode("utf-8", errors="ignore").decode("utf-8", errors="ignore")


CONFIG_PATH = Path(__file__).parent / "config.yaml"


def load_config():
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    config["memory_dir"] = Path(__file__).parent / config.get("memory_dir", "./memory")
    return config


# ============ 三层记忆系统 ============
class Memory:
    def __init__(self, memory_dir: Path):
        self.memory_dir = memory_dir
        self.memory_dir.mkdir(parents=True, exist_ok=True)

        self.hot_file = memory_dir / "hot.md"  # 会话级
        self.cold_file = memory_dir / "cold.md"  # 用户级
        self.agent_file = memory_dir / "agent.md"  # Agent级

        # 初始化
        if not self.hot_file.exists():
            self.hot_file.write_text(
                "# Hot Memory\n\n", encoding="utf-8", errors="ignore"
            )
        if not self.cold_file.exists():
            self.cold_file.write_text(
                "# Cold Memory (User)\n\n", encoding="utf-8", errors="ignore"
            )
        if not self.agent_file.exists():
            self.agent_file.write_text(
                "# Agent Memory\n\n", encoding="utf-8", errors="ignore"
            )

    # ===== 会话级 =====
    def add_hot(self, role: str, content: str, agent: str = None):
        """写入会话级记忆（每次对话）"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        tag = f"[{agent}]" if agent else ""
        entry = f"- **{timestamp}** {role}{tag}: {content[:60]}\n"

        content_file = safe_text(
            self.hot_file.read_text(encoding="utf-8", errors="ignore")
        )
        lines = [l for l in content_file.split("\n") if l.strip().startswith("- **")]
        lines.append(entry.strip())
        lines = lines[-15:]

        self.hot_file.write_text(
            "# Hot Memory\n\n" + "\n".join(lines), encoding="utf-8", errors="ignore"
        )

    def get_hot(self) -> str:
        return safe_text(self.hot_file.read_text(encoding="utf-8", errors="ignore"))

    def clear_hot(self):
        self.hot_file.write_text("# Hot Memory\n\n", encoding="utf-8", errors="ignore")

    def get_hot_for_llm(self) -> str:
        """给LLM用的会话记忆"""
        content = self.get_hot()
        lines = [l for l in content.split("\n") if l.strip().startswith("- **")]
        return "\n".join(lines[-5:])

    # ===== 用户级 =====
    def add_cold(self, content: str):
        """写入用户级记忆（LLM提取）"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = f"- **{timestamp}**: {content}\n"

        content_file = safe_text(
            self.cold_file.read_text(encoding="utf-8", errors="ignore")
        )
        lines = content_file.split("\n")

        # 插入到第一行
        insert_idx = 2
        lines.insert(insert_idx, entry)

        self.cold_file.write_text("\n".join(lines), encoding="utf-8", errors="ignore")

    def get_cold(self) -> str:
        return safe_text(self.cold_file.read_text(encoding="utf-8", errors="ignore"))

    # ===== Agent级 =====
    def add_agent(self, content: str):
        """写入Agent级记忆"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = f"- **{timestamp}**: {content}\n"

        content_file = safe_text(
            self.agent_file.read_text(encoding="utf-8", errors="ignore")
        )
        lines = content_file.split("\n")
        lines.insert(2, entry)

        self.agent_file.write_text("\n".join(lines), encoding="utf-8", errors="ignore")

    def get_agent(self) -> str:
        return safe_text(self.agent_file.read_text(encoding="utf-8", errors="ignore"))

    # ===== 语义搜索 =====
    def search(self, query: str) -> dict:
        """搜索三层记忆"""
        query_lower = query.lower()
        results = {"hot": [], "cold": [], "agent": []}

        # 搜索hot
        hot = self.get_hot()
        if query_lower in hot.lower():
            results["hot"] = [l for l in hot.split("\n") if query_lower in l.lower()][
                :3
            ]

        # 搜索cold
        cold = self.get_cold()
        if query_lower in cold.lower():
            results["cold"] = [l for l in cold.split("\n") if query_lower in l.lower()][
                :3
            ]

        # 搜索agent
        agent = self.get_agent()
        if query_lower in agent.lower():
            results["agent"] = [
                l for l in agent.split("\n") if query_lower in l.lower()
            ][:3]

        return results

    # ===== Evolve =====
    def evolve(self):
        """自进化system.md"""
        cold = self.get_cold()
        agent = self.get_agent()

        system_file = Path(__file__).parent / "prompts" / "system.md"
        content = safe_text(system_file.read_text(encoding="utf-8", errors="ignore"))

        # 提取经验
        exps = []
        for line in (cold + "\n" + agent).split("\n"):
            if line.strip().startswith("- **") and len(line) > 20:
                exp = line.strip()[3:50]
                if exp not in content:
                    exps.append(f"- {exp}")

        if exps:
            new_section = "\n## Evolved\n" + "\n".join(exps[:3]) + "\n"
            if "## Evolved" in content:
                content = re.sub(
                    r"## Evolved\n.*", new_section, content, flags=re.DOTALL
                )
            else:
                content += new_section
            system_file.write_text(content, encoding="utf-8", errors="ignore")
            print("[Evolve]")


# ============ LLM (Simulated) ============
def llm_extract_memory(query: str, response: str) -> str:
    """LLM提取重要记忆（模拟）"""
    # 模拟提取关键词
    keywords = ["jwt", "token", "auth", "api", "config", "error", "fix", "bug"]
    q = query.lower()
    for kw in keywords:
        if kw in q:
            return f"Topic: {kw}, Q: {query[:30]}"
    return f"Q: {query[:35]}"


def llm_response(query: str, memory: Memory) -> str:
    """LLM生成回答"""
    hot = memory.get_hot_for_llm()
    cold = memory.get_cold()
    agent = memory.get_agent()

    return f"""[Response]

Query: {query}

---

### Memory Context:
**Hot (Session)**: {len(hot)} chars
{cold[:100] if cold else "(empty)"}

**Cold (User)**: {len(cold)} chars
{cold[:100] if cold else "(empty)"}

**Agent**: {len(agent)} chars
{agent[:100] if agent else "(empty)"}

---
*Simulated*"""


# ============ Agent ============
class Shuohan:
    def __init__(self, config):
        self.config = config
        self.base_dir = Path(__file__).parent
        self.memory = Memory(config["memory_dir"])

        # 意外退出保存
        atexit.register(self._auto_save)

        self.memory.evolve()

    def _auto_save(self):
        try:
            hot = self.memory.get_hot()
            if len(hot) > 30:
                self.memory.add_cold(f"[Auto-save] {hot[:80]}")
        except:
            pass

    def run(self, user_input: str) -> str:
        # 1. 写入会话记忆
        self.memory.add_hot("user", user_input)

        # 2. 搜索记忆
        search_results = self.memory.search(user_input)

        # 3. LLM回答
        response = llm_response(user_input, self.memory)

        # 4. 写入会话记忆
        self.memory.add_hot("assistant", response, agent="硕含")

        # 5. 提取用户记忆（自动）
        extracted = llm_extract_memory(user_input, response)
        self.memory.add_cold(extracted)

        # 6. Evolve
        self.memory.evolve()

        return response


# ============ Main ============
def main():
    config = load_config()
    agent = Shuohan(config)

    print("\n" + "=" * 50)
    print("  硕含 Agent")
    print("  Hot: hot.md | Cold: cold.md | Agent: agent.md")
    print("  Type 'quit' to exit")
    print("=" * 50 + "\n")

    while True:
        try:
            user_input = input("You: ").strip()
            if not user_input:
                continue
            if user_input.lower() in ["quit", "exit", "q"]:
                print("\nBye!")
                break

            result = agent.run(user_input)
            print(f"\n{result}\n")

        except KeyboardInterrupt:
            print("\n\nBye!")
            break
        except Exception as e:
            print(f"\nError: {e}")


if __name__ == "__main__":
    main()
