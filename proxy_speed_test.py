"""
代理节点速度测试脚本
使用方法：
1. 在白鲸加速器中切换到某个节点
2. 运行此脚本：python proxy_speed_test.py
3. 记录结果，切换下一个节点，重复测试
"""

import time
import urllib.request
import socket

# 测试目标 (使用 HTTPS URL 进行 HTTP 延迟测试)
TEST_TARGETS = [
    ("Google", "https://www.google.com"),
    ("YouTube", "https://www.youtube.com"),
    ("GitHub", "https://github.com"),
    ("Cloudflare", "https://1.1.1.1"),
]

# 下载测速URL (小文件，用于测试下载速度)
SPEED_TEST_URL = "https://www.google.com/images/branding/googlelogo/2x/googlelogo_color_272x92dp.png"


def http_latency_test(url, timeout=10):
    """HTTP 请求测试延迟（比 Ping 更准确，不会被防火墙拦截）"""
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        start = time.time()
        response = urllib.request.urlopen(req, timeout=timeout)
        response.read(1024)  # 只读取少量数据
        elapsed = time.time() - start
        return int(elapsed * 1000)
    except Exception as e:
        return -1


def tcp_connect_test(host, port=443, timeout=5):
    """TCP 连接测试"""
    try:
        start = time.time()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))
        sock.close()
        return int((time.time() - start) * 1000)
    except:
        return -1


def download_speed_test(url=SPEED_TEST_URL, timeout=15):
    """下载速度测试"""
    try:
        start = time.time()
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        response = urllib.request.urlopen(req, timeout=timeout)
        data = response.read()
        elapsed = time.time() - start

        size_kb = len(data) / 1024
        speed_kbps = size_kb / elapsed

        return size_kb, elapsed, speed_kbps
    except Exception as e:
        return None, None, None


def main():
    print("=" * 60)
    print("        代理节点速度测试")
    print("=" * 60)
    print()

    # HTTP 延迟测试
    print("【HTTP 延迟测试】")
    print("-" * 40)
    results = []
    for name, url in TEST_TARGETS:
        print(f"  测试 {name}...", end=" ", flush=True)
        latency = http_latency_test(url)
        if latency > 0:
            print(f"{latency} ms")
            results.append((name, latency))
        else:
            print("超时/失败")
            results.append((name, -1))

    print()

    # TCP 连接测试
    print("【TCP 连接测试】")
    print("-" * 40)
    tcp_hosts = [
        ("Google", "www.google.com"),
        ("YouTube", "www.youtube.com"),
        ("GitHub", "github.com"),
        ("Cloudflare", "1.1.1.1"),
    ]
    for name, host in tcp_hosts:
        print(f"  连接 {name}:443...", end=" ", flush=True)
        latency = tcp_connect_test(host)
        if latency > 0:
            print(f"{latency} ms")
        else:
            print("失败")

    print()

    # 下载速度测试
    print("【下载速度测试】")
    print("-" * 40)
    print(f"  下载测试文件...", end=" ", flush=True)
    size, elapsed, speed = download_speed_test()
    if speed:
        print(f"完成")
        print(f"    文件大小: {size:.1f} KB")
        print(f"    下载耗时: {elapsed:.2f} 秒")
        print(f"    下载速度: {speed:.1f} KB/s")
    else:
        print("失败")

    print()
    print("=" * 60)

    # 汇总
    valid_results = [(n, l) for n, l in results if l > 0]
    if valid_results:
        avg_latency = sum(l for _, l in valid_results) / len(valid_results)
        print(f"  平均延迟: {avg_latency:.0f} ms")

        # 评价
        if avg_latency < 500:
            grade = "优秀 ★★★★★"
        elif avg_latency < 1000:
            grade = "良好 ★★★★☆"
        elif avg_latency < 2000:
            grade = "一般 ★★★☆☆"
        else:
            grade = "较差 ★★☆☆☆"
        print(f"  节点评价: {grade}")
    else:
        print("  无法连接到测试目标，请检查代理是否正常工作")

    print("=" * 60)
    print()
    input("按回车键退出...")


if __name__ == "__main__":
    main()
