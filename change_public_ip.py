"""
公网IP切换工具 - 用于测试
通过代理服务器更改浏览器识别的公网IP地址
支持HTTP/HTTPS/SOCKS5代理
"""

import requests
import os
import sys
import json
import time


class PublicIPChanger:
    """公网IP切换器 - 通过代理服务器"""

    def __init__(self):
        self.current_proxy = None
        self.session = requests.Session()

    def get_current_ip(self, use_proxy=False):
        """获取当前公网IP地址"""
        try:
            if use_proxy and self.current_proxy:
                response = self.session.get('https://api.ipify.org?format=json',
                                           proxies=self.current_proxy,
                                           timeout=10)
            else:
                response = requests.get('https://api.ipify.org?format=json', timeout=10)

            data = response.json()
            return data.get('ip')
        except Exception as e:
            print(f"❌ 获取IP失败: {e}")
            return None

    def get_ip_info(self, ip=None):
        """获取IP详细信息（地理位置等）"""
        try:
            if ip:
                url = f'http://ip-api.com/json/{ip}?lang=zh-CN'
            else:
                url = 'http://ip-api.com/json/?lang=zh-CN'

            response = requests.get(url, timeout=10)
            data = response.json()

            if data.get('status') == 'success':
                print(f"\n📍 IP信息:")
                print(f"  IP地址: {data.get('query')}")
                print(f"  国家: {data.get('country')}")
                print(f"  地区: {data.get('regionName')}")
                print(f"  城市: {data.get('city')}")
                print(f"  ISP: {data.get('isp')}")
                print(f"  时区: {data.get('timezone')}")
                return data
            else:
                print(f"❌ 无法获取IP信息")
                return None

        except Exception as e:
            print(f"❌ 获取IP信息失败: {e}")
            return None

    def set_proxy(self, proxy_type, host, port, username=None, password=None):
        """
        设置代理服务器

        Args:
            proxy_type: 代理类型 (http, https, socks5)
            host: 代理服务器地址
            port: 端口号
            username: 用户名 (可选)
            password: 密码 (可选)
        """
        try:
            if username and password:
                proxy_url = f"{proxy_type}://{username}:{password}@{host}:{port}"
            else:
                proxy_url = f"{proxy_type}://{host}:{port}"

            self.current_proxy = {
                'http': proxy_url,
                'https': proxy_url
            }

            print(f"\n✅ 代理已设置: {proxy_type}://{host}:{port}")

            # 测试代理
            print("\n🔍 测试代理连接...")
            new_ip = self.get_current_ip(use_proxy=True)

            if new_ip:
                print(f"✅ 代理连接成功!")
                print(f"🌐 新的公网IP: {new_ip}")
                self.get_ip_info(new_ip)
                return True
            else:
                print("❌ 代理连接失败")
                self.current_proxy = None
                return False

        except Exception as e:
            print(f"❌ 设置代理失败: {e}")
            self.current_proxy = None
            return False

    def remove_proxy(self):
        """移除代理，恢复直连"""
        self.current_proxy = None
        self.session = requests.Session()
        print("\n✅ 已移除代理，恢复直连")

        print("\n🔍 当前公网IP:")
        current_ip = self.get_current_ip()
        if current_ip:
            print(f"🌐 IP地址: {current_ip}")
            self.get_ip_info(current_ip)

    def test_proxy_list(self, proxy_list):
        """
        测试多个代理服务器

        Args:
            proxy_list: 代理列表 [{'type': 'http', 'host': '1.1.1.1', 'port': 8080}, ...]
        """
        print(f"\n🔍 开始测试 {len(proxy_list)} 个代理服务器...")
        print("-" * 60)

        working_proxies = []

        for i, proxy in enumerate(proxy_list, 1):
            print(f"\n[{i}/{len(proxy_list)}] 测试: {proxy['host']}:{proxy['port']}")

            try:
                proxy_url = f"{proxy['type']}://{proxy['host']}:{proxy['port']}"
                test_proxy = {
                    'http': proxy_url,
                    'https': proxy_url
                }

                response = requests.get('https://api.ipify.org?format=json',
                                      proxies=test_proxy,
                                      timeout=5)

                if response.status_code == 200:
                    ip = response.json().get('ip')
                    print(f"  ✅ 可用 - IP: {ip}")
                    working_proxies.append({**proxy, 'ip': ip})
                else:
                    print(f"  ❌ 不可用")

            except Exception as e:
                print(f"  ❌ 连接失败: {str(e)[:50]}")

        print(f"\n{'='*60}")
        print(f"📊 测试完成: {len(working_proxies)}/{len(proxy_list)} 个代理可用")

        if working_proxies:
            print("\n✅ 可用代理列表:")
            for i, proxy in enumerate(working_proxies, 1):
                print(f"{i}. {proxy['host']}:{proxy['port']} - IP: {proxy['ip']}")

        return working_proxies

    def set_system_proxy_windows(self, host, port):
        """设置Windows系统代理（影响整个系统和浏览器）"""
        try:
            import winreg

            # 设置注册表
            internet_settings = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r'Software\Microsoft\Windows\CurrentVersion\Internet Settings',
                0, winreg.KEY_ALL_ACCESS
            )

            # 启用代理
            winreg.SetValueEx(internet_settings, 'ProxyEnable', 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(internet_settings, 'ProxyServer', 0, winreg.REG_SZ, f"{host}:{port}")

            winreg.CloseKey(internet_settings)

            print(f"\n✅ Windows系统代理已设置: {host}:{port}")
            print("⚠️  浏览器将使用此代理访问网络")
            print("💡 记得使用后关闭系统代理!")

            return True

        except Exception as e:
            print(f"❌ 设置系统代理失败: {e}")
            print("💡 可能需要管理员权限")
            return False

    def remove_system_proxy_windows(self):
        """关闭Windows系统代理"""
        try:
            import winreg

            internet_settings = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r'Software\Microsoft\Windows\CurrentVersion\Internet Settings',
                0, winreg.KEY_ALL_ACCESS
            )

            # 禁用代理
            winreg.SetValueEx(internet_settings, 'ProxyEnable', 0, winreg.REG_DWORD, 0)

            winreg.CloseKey(internet_settings)

            print("\n✅ Windows系统代理已关闭")
            print("🌐 浏览器已恢复直连")

            return True

        except Exception as e:
            print(f"❌ 关闭系统代理失败: {e}")
            return False

    def generate_curl_command(self, url="https://api.ipify.org"):
        """生成使用当前代理的curl命令"""
        if not self.current_proxy:
            print("❌ 未设置代理")
            return

        proxy_url = self.current_proxy.get('http', '')

        print(f"\n📋 使用代理的curl命令:")
        print(f"curl -x {proxy_url} {url}")


def load_free_proxy_list():
    """获取免费代理列表（示例）"""
    # 这里是示例代理，实际使用时需要替换为真实可用的代理
    print("\n⚠️  注意: 免费代理通常不稳定，建议使用付费代理服务")
    print("💡 推荐代理服务: ")
    print("   - 国内: 快代理、芝麻代理、阿布云")
    print("   - 国外: Bright Data、Oxylabs、SmartProxy")

    return []


def main():
    """主函数 - 交互式菜单"""
    changer = PublicIPChanger()

    print("\n" + "="*60)
    print("🌐 当前公网IP信息:")
    print("="*60)
    current_ip = changer.get_current_ip()
    if current_ip:
        print(f"IP地址: {current_ip}")
        changer.get_ip_info(current_ip)

    while True:
        print("\n" + "="*60)
        print("🌐 公网IP切换工具 (测试用)")
        print("="*60)
        print("1. 查看当前公网IP")
        print("2. 设置HTTP/HTTPS代理")
        print("3. 设置SOCKS5代理")
        print("4. 移除代理（恢复直连）")
        print("5. 测试代理列表")
        print("6. 设置Windows系统代理（影响浏览器）")
        print("7. 关闭Windows系统代理")
        print("8. 获取免费代理信息")
        print("0. 退出")
        print("="*60)

        choice = input("\n请选择操作 (0-8): ").strip()

        if choice == '0':
            print("\n👋 再见!")
            break

        elif choice == '1':
            print("\n🔍 查询公网IP...")
            if changer.current_proxy:
                print("(使用代理)")
                ip = changer.get_current_ip(use_proxy=True)
            else:
                print("(直连)")
                ip = changer.get_current_ip()

            if ip:
                print(f"\n🌐 当前IP: {ip}")
                changer.get_ip_info(ip)

        elif choice == '2':
            print("\n🔧 设置HTTP/HTTPS代理")
            host = input("代理服务器地址: ").strip()
            port = input("端口号: ").strip()
            username = input("用户名 (留空跳过): ").strip() or None
            password = input("密码 (留空跳过): ").strip() or None

            changer.set_proxy('http', host, port, username, password)

        elif choice == '3':
            print("\n🔧 设置SOCKS5代理")
            host = input("代理服务器地址: ").strip()
            port = input("端口号: ").strip()
            username = input("用户名 (留空跳过): ").strip() or None
            password = input("密码 (留空跳过): ").strip() or None

            changer.set_proxy('socks5', host, port, username, password)

        elif choice == '4':
            changer.remove_proxy()

        elif choice == '5':
            print("\n📋 请输入代理列表 (格式: type,host,port)")
            print("示例: http,1.2.3.4,8080")
            print("输入 'done' 完成输入")

            proxy_list = []
            while True:
                line = input("代理 > ").strip()
                if line.lower() == 'done':
                    break

                try:
                    parts = line.split(',')
                    if len(parts) >= 3:
                        proxy_list.append({
                            'type': parts[0].strip(),
                            'host': parts[1].strip(),
                            'port': parts[2].strip()
                        })
                except:
                    print("格式错误，请重新输入")

            if proxy_list:
                working = changer.test_proxy_list(proxy_list)

                if working:
                    use = input("\n是否使用第一个可用代理? (y/n): ").lower()
                    if use == 'y':
                        p = working[0]
                        changer.set_proxy(p['type'], p['host'], p['port'])

        elif choice == '6':
            print("\n⚠️  这将设置Windows系统代理，影响所有浏览器")
            host = input("代理服务器地址: ").strip()
            port = input("端口号: ").strip()

            confirm = input(f"确认设置系统代理 {host}:{port}? (y/n): ").lower()
            if confirm == 'y':
                changer.set_system_proxy_windows(host, port)

        elif choice == '7':
            changer.remove_system_proxy_windows()

        elif choice == '8':
            print("\n💡 如何获取代理:")
            print("-" * 60)
            print("1. 付费代理服务 (推荐):")
            print("   - 快代理: https://www.kuaidaili.com/")
            print("   - 芝麻代理: http://www.zhimaruanjian.com/")
            print("   - 阿布云: https://www.abuyun.com/")
            print("\n2. 免费代理列表网站:")
            print("   - https://www.89ip.cn/")
            print("   - https://www.zdaye.com/")
            print("   - https://proxy-list.download/")
            print("\n3. VPN服务 (更稳定):")
            print("   - 更适合长期使用")
            print("   - 提供多国家IP")

            load_free_proxy_list()

        else:
            print("❌ 无效选择")


def quick_test():
    """快速测试示例"""
    print("\n📝 快速测试示例:")
    print("-" * 60)

    changer = PublicIPChanger()

    # 显示当前IP
    print("\n1️⃣ 当前真实IP:")
    current_ip = changer.get_current_ip()
    if current_ip:
        print(f"IP: {current_ip}")
        changer.get_ip_info(current_ip)

    # 示例：设置代理（需要有效的代理服务器）
    # changer.set_proxy('http', '代理服务器', '端口')


if __name__ == "__main__":
    print("\n" + "="*60)
    print("🚀 公网IP切换工具启动中...")
    print("="*60)
    print("⚠️  注意: 需要配置代理服务器才能更改公网IP")
    print("💡 此工具通过代理服务器来改变浏览器识别的IP地址")
    print("🔒 仅用于合法测试目的")

    main()
