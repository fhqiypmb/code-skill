// 硕含记忆系统插件 - v5.1 (生产环境纯净版)
const { tool } = require("@opencode-ai/plugin");
const { spawnSync } = require("child_process");
const path = require("path");

function runMemoryOps(directory, args) {
  const scriptPath = path.join(directory, ".opencode", "skills", "shuohan", "memory_ops.py");
  const isWindows = process.platform === 'win32';

  const options = {
    encoding: "utf-8",
    cwd: directory,
    shell: true,
    timeout: 5000
  };

  try {
    const cmd = isWindows
      ? `chcp 65001 > nul && python "${scriptPath}" ${args}`
      : `python3 "${scriptPath}" ${args}`;

    const result = spawnSync(cmd, options);
    return result.stdout ? result.stdout.trim() : "";
  } catch (e) {
    return "";
  }
}

module.exports = async function MemoryPlugin(context) {
  return {
    tool: {
      memory_read: tool({
        description: "读取记忆文件 (hot/cold/agent)",
        args: {
          type: tool.schema.string().enum(["hot", "cold", "agent"]).describe("类型")
        },
        execute: async (args, ctx) => {
          return runMemoryOps(ctx.directory, `read ${args.type}`);
        }
      }),
      memory_write: tool({
        description: "写入记忆条目 (手动/自动存盘)",
        args: {
          type: tool.schema.string().enum(["hot", "cold", "agent"]).describe("类型"),
          content: tool.schema.string().describe("内容")
        },
        execute: async (args, ctx) => {
          const safeContent = args.content.replace(/"/g, "'");
          return runMemoryOps(ctx.directory, `write ${args.type} "${safeContent}"`);
        }
      })
    }
  };
};
