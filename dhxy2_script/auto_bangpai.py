"""
大话西游2 - 自动帮派脚本
流程：
1. Alt+Q 打开任务界面
2. 点击"日常任务"展开
3. 点击"帮派任务"
4. 点击"帮派总管"
"""

import ctypes
import ctypes.wintypes
import time
import os

try:
    import pyautogui
    import cv2
    import numpy as np
    from PIL import ImageGrab
except ImportError:
    print("请先安装依赖: pip install pyautogui opencv-python pillow numpy")
    exit(1)

# Windows API 结构体定义
user32 = ctypes.windll.user32

# SendInput 相关常量
INPUT_KEYBOARD = 1
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_KEYUP = 0x0002

# 扫描码
SCAN_ALT = 0x38
SCAN_Q = 0x10

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.wintypes.WORD),
        ("wScan", ctypes.wintypes.WORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))
    ]

class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.wintypes.DWORD),
        ("ki", KEYBDINPUT),
        ("padding", ctypes.c_ubyte * 8)
    ]

# 脚本目录
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
IMAGES_DIR = os.path.join(SCRIPT_DIR, "images")

# 配置
DELAY_SECONDS = 2
CLICK_DELAY = 0.5  # 点击后等待时间



def find_hyperv_window():
    """查找 Hyper-V 虚拟机窗口"""
    result = []

    def enum_callback(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buff = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buff, length + 1)
                title = buff.value
                if "虚拟机连接" in title or "Virtual Machine Connection" in title:
                    result.append((hwnd, title))
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    user32.EnumWindows(WNDENUMPROC(enum_callback), 0)
    return result

def get_window_rect(hwnd):
    """获取窗口位置和大小"""
    rect = ctypes.wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom

def click_at(x, y):
    """点击指定屏幕坐标"""
    print(f"  -> 点击坐标 ({x}, {y}) ...")
    x = int(x)
    y = int(y)
    user32.SetCursorPos(x, y)
    time.sleep(0.1)
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.05)
    user32.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(CLICK_DELAY)

def send_key_down(scan_code):
    """按下键（使用扫描码）"""
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.ki.wVk = 0
    inp.ki.wScan = scan_code
    inp.ki.dwFlags = KEYEVENTF_SCANCODE
    inp.ki.time = 0
    inp.ki.dwExtraInfo = None
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

def send_key_up(scan_code):
    """释放键（使用扫描码）"""
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.ki.wVk = 0
    inp.ki.wScan = scan_code
    inp.ki.dwFlags = KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP
    inp.ki.time = 0
    inp.ki.dwExtraInfo = None
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

def send_alt_q():
    """发送 Alt+Q（使用扫描码）"""
    print("  -> 发送 Alt+Q ...")
    send_key_down(SCAN_ALT)
    time.sleep(0.05)
    send_key_down(SCAN_Q)
    time.sleep(0.05)
    send_key_up(SCAN_Q)
    time.sleep(0.05)
    send_key_up(SCAN_ALT)
    time.sleep(1)  # 等待界面打开

def find_image_on_screen(template_path, confidence=0.8):
    """在屏幕上查找图像，返回中心坐标。支持偏色和多尺度匹配。"""
    if not os.path.exists(template_path):
        print(f"  错误: 模板图像不存在 {template_path}")
        return None

    # 截取屏幕
    screenshot = ImageGrab.grab()
    screenshot_np = np.array(screenshot)

    # 读取模板（彩色）
    template_bgr = cv2.imread(template_path)
    if template_bgr is None:
        print(f"  错误: 无法读取模板 {template_path}")
        return None

    # 缩放范围：0.7x ~ 1.3x，步长0.1
    scales = [round(s, 1) for s in np.arange(0.7, 1.35, 0.1)]

    best_val = -1
    best_loc = None
    best_w = 0
    best_h = 0
    best_scale = 1.0
    best_method = ""

    # 预处理截图：灰度 + 边缘
    screenshot_gray = cv2.cvtColor(screenshot_np, cv2.COLOR_RGB2GRAY)
    screenshot_edges = cv2.Canny(screenshot_gray, 50, 150)

    # 预处理模板原始尺寸：灰度 + 边缘
    template_rgb = cv2.cvtColor(template_bgr, cv2.COLOR_BGR2RGB)
    template_gray_orig = cv2.cvtColor(template_bgr, cv2.COLOR_BGR2GRAY)
    template_edges_orig = cv2.Canny(template_gray_orig, 50, 150)

    for scale in scales:
        # 缩放模板
        new_w = int(template_bgr.shape[1] * scale)
        new_h = int(template_bgr.shape[0] * scale)
        if new_w < 5 or new_h < 5:
            continue
        if new_w > screenshot_np.shape[1] or new_h > screenshot_np.shape[0]:
            continue

        template_gray = cv2.resize(template_gray_orig, (new_w, new_h), interpolation=cv2.INTER_AREA)
        template_edges = cv2.resize(template_edges_orig, (new_w, new_h), interpolation=cv2.INTER_AREA)

        # 方法1: 灰度模板匹配（应对轻微偏色）
        result = cv2.matchTemplate(screenshot_gray, template_gray, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        if max_val > best_val:
            best_val = max_val
            best_loc = max_loc
            best_w, best_h = new_w, new_h
            best_scale = scale
            best_method = "灰度匹配"

        # 方法2: 边缘匹配（抗偏色，只关注轮廓）
        result_edge = cv2.matchTemplate(screenshot_edges, template_edges, cv2.TM_CCOEFF_NORMED)
        _, max_val_e, _, max_loc_e = cv2.minMaxLoc(result_edge)
        if max_val_e > best_val:
            best_val = max_val_e
            best_loc = max_loc_e
            best_w, best_h = new_w, new_h
            best_scale = scale
            best_method = "边缘匹配"

    if best_val >= confidence:
        center_x = best_loc[0] + best_w // 2
        center_y = best_loc[1] + best_h // 2
        print(f"  找到图像! [{best_method}] 置信度: {best_val:.2f}, 缩放: {best_scale:.1f}x, 位置: ({center_x}, {center_y})")
        return (center_x, center_y)
    else:
        print(f"  未找到图像, 最高置信度: {best_val:.2f} [{best_method}]")
        return None

def check_if_daily_task_expanded():
    """判断日常任务是否展开"""
    print("  -> 检查日常任务是否已展开...")
    # 如果能找到"帮派任务"按钮，说明已展开
    bangpai_path = os.path.join(IMAGES_DIR, "bangpai_task.png")
    pos = find_image_on_screen(bangpai_path, confidence=0.7)

    if pos:
        print("  -> 找到'帮派任务'，说明已展开")
        return True
    else:
        print("  -> 未找到'帮派任务'，说明未展开")
        return False

def click_scroll_arrow():
    """点击向下箭头来滚动任务列表"""
    arrow_path = os.path.join(IMAGES_DIR, "scroll_down_arrow.png")
    print(f"  -> 查找向下箭头...")

    pos = find_image_on_screen(arrow_path, confidence=0.5)
    if pos:
        click_at(pos[0], pos[1])
        return True
    else:
        print(f"  -> 未找到向下箭头")
        return False

def click_image(template_name, confidence=0.8, wait_time=1, max_scrolls=3):
    """查找并点击图像，如果找不到则向下滚动再找"""
    template_path = os.path.join(IMAGES_DIR, template_name)
    print(f"\n查找: {template_name}")

    # 先尝试不滚动直接查找
    pos = find_image_on_screen(template_path, confidence)
    if pos:
        click_at(pos[0], pos[1])
        time.sleep(wait_time)
        return True

    # 如果找不到，则点击箭头向下滚动几次再查找
    print(f"  -> 未直接找到，开始点击箭头查找...")
    for scroll_count in range(max_scrolls):
        print(f"  -> 点击箭头第 {scroll_count + 1} 次...")
        click_scroll_arrow()
        time.sleep(0.5)

        pos = find_image_on_screen(template_path, confidence)
        if pos:
            click_at(pos[0], pos[1])
            time.sleep(wait_time)
            return True

    print(f"  -> 点击箭头 {max_scrolls} 次后仍未找到")
    return False

def main():
    print("=" * 50)
    print("  大话西游2 - 自动帮派脚本")
    print("=" * 50)

    # 检查图像模板
    required_images = ["daily_task.png", "bangpai_task.png", "bangpai_zongguan.png"]
    missing = [img for img in required_images if not os.path.exists(os.path.join(IMAGES_DIR, img))]

    if missing:
        print("\n缺少以下图像模板，请先截图保存到 images 目录:")
        for img in missing:
            print(f"  - {img}")
        print("\n提示: 截取按钮区域保存为对应文件名")
        print(f"图像目录: {IMAGES_DIR}")
        return

    print("\n正在查找 Hyper-V 虚拟机窗口...")
    windows = find_hyperv_window()

    if not windows:
        print("错误: 未找到 Hyper-V 虚拟机窗口!")
        return

    hwnd, title = windows[0]
    print(f"找到窗口: {title}")

    left, top, right, bottom = get_window_rect(hwnd)
    center_x = (left + right) // 2
    center_y = (top + bottom) // 2

    print(f"\n{DELAY_SECONDS} 秒后执行...")
    for i in range(DELAY_SECONDS, 0, -1):
        print(f"  倒计时: {i} 秒...")
        time.sleep(1)

    # 点击窗口中心激活
    click_at(center_x, center_y)

    # 1. 发送 Alt+Q 打开任务界面
    send_alt_q()

    # 2. 判断日常任务是否已展开
    print("\n检查日常任务状态...")
    # 使用 OCR 判断日常任务是否已展开
    is_expanded = check_if_daily_task_expanded()

    if not is_expanded:
        print("  -> 日常任务未展开，点击展开...")
        click_image("daily_task.png", wait_time=0.8)
    else:
        print("  -> 日常任务已展开，无需点击")

    # 3. 点击帮派任务
    if not click_image("bangpai_task.png"):
        print("错误: 找不到帮派任务按钮")
        return

    # 4. 点击帮派总管
    if not click_image("bangpai_zongguan.png"):
        print("错误: 找不到帮派总管按钮")
        return

    print("\n完成!")

if __name__ == "__main__":
    main()
