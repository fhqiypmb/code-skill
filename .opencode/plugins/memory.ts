import { tool } from "@opencode-ai/plugin";
import { execSync } from "child_process";
import path from "path";

// 执行记忆操作脚本
function runMemoryOps(directory: string, args: string): string {
  const scriptPath = path.join(directory, ".opencode", "skills", "shuohan", "memory_ops.py");
  try {
    return execSync(`python "${scriptPath}" ${args}`, {
      encoding: "utf-8",
      cwd: directory,
      timeout: 5000
    });
  } catch (e: any) {
    return `Error: ${e.message}`;
  }
}

// 导出工具定义
export const memory_read = tool({
  description: "读取记忆(hot/cold/agent)",
  args: {
    type: tool.schema.string().describe("记忆类型: hot, cold, 或 agent")
  },
  execute: async (args, context) => {
    return runMemoryOps(context.directory, `read ${args.type}`);
  }
});

export const memory_write = tool({
  description: "写入记忆",
  args: {
    type: tool.schema.string().describe("记忆类型: hot, cold, 或 agent"),
    content: tool.schema.string().describe("要写入的内容")
  },
  execute: async (args, context) => {
    const escaped = args.content.replace(/"/g, "'");
    return runMemoryOps(context.directory, `write ${args.type} "${escaped}"`);
  }
});

export const memory_search = tool({
  description: "搜索记忆",
  args: {
    query: tool.schema.string().describe("搜索关键词")
  },
  execute: async (args, context) => {
    return runMemoryOps(context.directory, `search "${args.query}"`);
  }
});
