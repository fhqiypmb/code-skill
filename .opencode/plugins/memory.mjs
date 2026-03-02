// 硕含记忆系统插件 - v5.3 (ESM修复版)
import { tool } from "@opencode-ai/plugin";
import { spawnSync } from "child_process";
import path from "path";

function runMemoryOps(directory, args) {
  const scriptPath = path.join(directory, ".opencode", "skills", "shuohan", "memory_ops.py");
  const isWindows = process.platform === 'win32';

  try {
    let result;
    
    if (isWindows) {
      // Windows: 使用cmd /c执行
      result = spawnSync(
        'cmd',
        ['/c', 'python', scriptPath, ...args.split(' ')],
        {
          encoding: "utf-8",
          cwd: directory,
          timeout: 5000,
          env: {
            ...process.env,
            PYTHONIOENCODING: "utf-8"
          }
        }
      );
    } else {
      // Linux/Mac: 直接执行
      result = spawnSync(
        'python3',
        [scriptPath, ...args.split(' ')],
        {
          encoding: "utf-8",
          cwd: directory,
          timeout: 5000,
          env: {
            ...process.env,
            PYTHONIOENCODING: "utf-8"
          }
        }
      );
    }

    if (result.error) {
      return `Error: ${result.error.message}`;
    }
    
    return result.stdout ? result.stdout.trim() : "";
  } catch (e) {
    return `Exception: ${e.message}`;
  }
}

export default async function MemoryPlugin(context) {
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
        description: "写入记忆条目",
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
}
