// 硕含记忆系统插件 - CommonJS格式
const { tool } = require("@opencode-ai/plugin");
const { execSync } = require("child_process");
const path = require("path");

// 执行记忆操作脚本
function runMemoryOps(directory, args) {
  const scriptPath = path.join(directory, ".opencode", "skills", "shuohan", "memory_ops.py");
  try {
    // Windows: 使用 chcp 65001 切换到UTF-8
    // Linux/Mac: 使用空命令
    const isWindows = process.platform === 'win32';
    const cmd = isWindows 
      ? `chcp 65001 > nul && python "${scriptPath}" ${args}`
      : `python3 "${scriptPath}" ${args}`;
    
    return execSync(cmd, {
      encoding: "utf-8",
      cwd: directory,
      timeout: 5000
    });
  } catch (e) {
    return `Error: ${e.message}`;
  }
}

// 导出插件
module.exports = async function MemoryPlugin(context) {
  return {
    tool: {
      memory_read: tool({
        description: "读取记忆(hot/cold/agent)",
        args: {
          type: tool.schema.string().describe("记忆类型: hot, cold, 或 agent")
        },
        execute: async (args, ctx) => {
          return runMemoryOps(ctx.directory, `read ${args.type}`);
        }
      }),

      memory_write: tool({
        description: "写入记忆",
        args: {
          type: tool.schema.string().describe("记忆类型: hot, cold, 或 agent"),
          content: tool.schema.string().describe("要写入的内容")
        },
        execute: async (args, ctx) => {
          const escaped = args.content.replace(/"/g, "'");
          return runMemoryOps(ctx.directory, `write ${args.type} "${escaped}"`);
        }
      }),

      memory_search: tool({
        description: "搜索记忆",
        args: {
          query: tool.schema.string().describe("搜索关键词")
        },
        execute: async (args, ctx) => {
          return runMemoryOps(ctx.directory, `search "${args.query}"`);
        }
      })
    }
  };
};
