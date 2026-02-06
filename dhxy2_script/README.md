# 大话西游2 自动帮派脚本

## 环境准备

```bash
cd dhxy2_script
pip install -r requirements.txt
```

## 使用方法

1. **启动脚本**
   ```bash
   python auto_bangpai.py
   ```

2. **操作步骤**
   - 切换到 Hyper-V 虚拟机窗口
   - 点击选中大话西游2游戏界面
   - 按 `Ctrl+Q` 开始自动帮派任务
   - 再次按 `Ctrl+Q` 暂停任务
   - 按 `Ctrl+C` 退出程序

## 注意事项

- 脚本需要以管理员权限运行（keyboard 库需要）
- 确保游戏窗口在虚拟机中处于激活状态
- 使用 Hyper-V 虚拟机可避免游戏检测

## 文件说明

- `auto_bangpai.py` - 主脚本
- `requirements.txt` - Python 依赖
