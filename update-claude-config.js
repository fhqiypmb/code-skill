#!/usr/bin/env node

const fs = require('fs');
const path = require('path');
const readline = require('readline');
const os = require('os');

// 获取 .claude 配置目录路径（跨平台）
function getClaudeConfigPath() {
  const homeDir = os.homedir();
  return path.join(homeDir, '.claude', 'settings.json');
}

// 创建命令行输入接口
const rl = readline.createInterface({
  input: process.stdin,
  output: process.stdout
});

// 提示输入
function question(query) {
  return new Promise(resolve => rl.question(query, resolve));
}

async function updateClaudeConfig() {
  try {
    const configPath = getClaudeConfigPath();
    console.log(`配置文件路径: ${configPath}\n`);

    // 检查配置文件是否存在
    if (!fs.existsSync(configPath)) {
      console.error(`错误: 配置文件不存在 ${configPath}`);
      rl.close();
      return;
    }

    // 读取当前配置
    const configContent = fs.readFileSync(configPath, 'utf-8');
    const config = JSON.parse(configContent);

    console.log('当前配置:');
    console.log(`API Key: ${config.env.ANTHROPIC_API_KEY}`);
    console.log(`Base URL: ${config.env.ANTHROPIC_BASE_URL}\n`);

    // 输入新的 API Key
    const newApiKey = await question('请输入新的 API Key: ');
    if (!newApiKey.trim()) {
      console.log('API Key 不能为空，操作已取消');
      rl.close();
      return;
    }

    // 询问是否替换 URL
    const defaultUrl = 'https://api-code.deepvlab.ai/anthropic';
    const replaceUrl = await question(`是否替换 Base URL? (y/n，默认 n): `);

    let newBaseUrl = config.env.ANTHROPIC_BASE_URL;
    if (replaceUrl.toLowerCase() === 'y') {
      const inputUrl = await question(`请输入新的 Base URL (默认: ${defaultUrl}): `);
      newBaseUrl = inputUrl.trim() || defaultUrl;
    }

    // 更新配置
    config.env.ANTHROPIC_API_KEY = newApiKey.trim();
    config.env.ANTHROPIC_BASE_URL = newBaseUrl;

    // 写入配置文件（格式化为 2 空格缩进）
    fs.writeFileSync(configPath, JSON.stringify(config, null, 2), 'utf-8');

    console.log('\n✅ 配置更新成功!');
    console.log('\n新配置:');
    console.log(`API Key: ${config.env.ANTHROPIC_API_KEY}`);
    console.log(`Base URL: ${config.env.ANTHROPIC_BASE_URL}`);

  } catch (error) {
    console.error('错误:', error.message);
  } finally {
    rl.close();
  }
}

// 运行
updateClaudeConfig();
