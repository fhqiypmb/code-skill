"""
网络IP修改工具 - 仅用于测试
智能推荐功能：自动分析当前网络并推荐合适的测试IP
支持Windows系统的网络适配器IP地址修改
"""

import subprocess
import os
import sys
import re
import ipaddress


class NetworkIPChanger:
    """网络IP修改器"""

    def __init__(self):
        self.check_admin()

    def check_admin(self):
        """检查是否具有管理员权限"""
        try:
            import ctypes
            is_admin = ctypes.windll.shell32.IsUserAnAdmin()
            if not is_admin:
                print("⚠️  警告: 需要管理员权限才能修改网络配置")
                print("请以管理员身份运行此脚本")
                return False
            return True
        except:
            return False

    def get_current_network_info(self):
        """获取当前网络信息"""
        try:
            result = subprocess.run(
                ['netsh', 'interface', 'ip', 'show', 'config'],
                capture_output=True,
                text=True,
                encoding='gbk'
            )
            return result.stdout
        except:
            return ""

    def parse_network_config(self, config_text):
        """解析网络配置，提取IP、网关、子网掩码等信息"""
        adapters = {}
        current_adapter = None

        for line in config_text.split('\n'):
            line = line.strip()

            # 匹配适配器名称
            if '配置' in line and '"' in line:
                match = re.search(r'"([^"]+)"', line)
                if match:
                    current_adapter = match.group(1)
                    adapters[current_adapter] = {}

            # 提取IP地址
            elif 'IP 地址' in line or 'IP Address' in line:
                ip_match = re.search(r'(\d+\.\d+\.\d+\.\d+)', line)
                if ip_match and current_adapter:
                    adapters[current_adapter]['ip'] = ip_match.group(1)

            # 提取子网掩码
            elif '子网前缀' in line or 'Subnet Prefix' in line:
                mask_match = re.search(r'/(\d+)', line)
                if mask_match and current_adapter:
                    prefix_len = int(mask_match.group(1))
                    adapters[current_adapter]['prefix'] = prefix_len
                    adapters[current_adapter]['mask'] = self.prefix_to_netmask(prefix_len)

            # 提取网关
            elif '默认网关' in line or 'Default Gateway' in line:
                gateway_match = re.search(r'(\d+\.\d+\.\d+\.\d+)', line)
                if gateway_match and current_adapter:
                    adapters[current_adapter]['gateway'] = gateway_match.group(1)

            # 检测DHCP状态
            elif 'DHCP' in line and '是' in line:
                if current_adapter:
                    adapters[current_adapter]['dhcp'] = True

        return adapters

    def prefix_to_netmask(self, prefix_len):
        """将前缀长度转换为子网掩码"""
        mask_map = {
            24: "255.255.255.0",
            16: "255.255.0.0",
            8: "255.0.0.0",
            25: "255.255.255.128",
            26: "255.255.255.192",
            27: "255.255.255.224",
            28: "255.255.255.240",
        }
        return mask_map.get(prefix_len, "255.255.255.0")

    def suggest_test_ip(self, adapter_info):
        """根据当前网络配置推荐测试IP"""
        if not adapter_info or 'ip' not in adapter_info:
            return None

        current_ip = adapter_info['ip']
        gateway = adapter_info.get('gateway', '')

        try:
            ip_parts = current_ip.split('.')
            suggested_ips = []

            # 策略1: 当前IP的末位 +10
            last_octet = int(ip_parts[3])
            if last_octet + 10 <= 254:
                suggested_ips.append(f"{ip_parts[0]}.{ip_parts[1]}.{ip_parts[2]}.{last_octet + 10}")

            # 策略2: 使用100段
            suggested_ips.append(f"{ip_parts[0]}.{ip_parts[1]}.{ip_parts[2]}.100")

            # 策略3: 使用200段
            suggested_ips.append(f"{ip_parts[0]}.{ip_parts[1]}.{ip_parts[2]}.200")

            # 策略4: 使用150段
            suggested_ips.append(f"{ip_parts[0]}.{ip_parts[1]}.{ip_parts[2]}.150")

            # 去除重复、当前IP和网关
            seen = set()
            unique_ips = []
            for ip in suggested_ips:
                if ip not in seen and ip != current_ip and ip != gateway:
                    seen.add(ip)
                    unique_ips.append(ip)

            return unique_ips[:3]  # 返回最多3个推荐
        except:
            return None

    def smart_suggest_and_set(self):
        """🎯 智能推荐并设置IP - 一键式向导"""
        print("\n" + "="*60)
        print("🎯 智能IP设置向导")
        print("="*60)

        # 获取网络配置
        config_text = self.get_current_network_info()
        adapters = self.parse_network_config(config_text)

        if not adapters:
            print("❌ 无法获取网络适配器信息")
            print("\n正在显示详细网络信息...")
            self.list_network_adapters()
            return

        # 显示可用适配器
        print("\n📡 检测到以下网络适配器:")
        print("-" * 60)
        adapter_list = list(adapters.keys())
        for i, name in enumerate(adapter_list, 1):
            info = adapters[name]
            current_ip = info.get('ip', '未配置')
            dhcp_status = "🔄 DHCP" if info.get('dhcp') else "📌 静态"
            print(f"{i}. {name}")
            print(f"   IP: {current_ip} ({dhcp_status})")
            if 'gateway' in info:
                print(f"   网关: {info['gateway']}")

        # 选择适配器
        try:
            choice = input(f"\n请选择要修改的适配器 (1-{len(adapter_list)}): ").strip()
            choice_num = int(choice)
            if choice_num < 1 or choice_num > len(adapter_list):
                print("❌ 无效选择")
                return

            selected_adapter = adapter_list[choice_num - 1]
            adapter_info = adapters[selected_adapter]

        except ValueError:
            print("❌ 输入无效")
            return

        # 显示当前配置
        print(f"\n{'='*60}")
        print(f"📋 当前配置: {selected_adapter}")
        print(f"{'='*60}")
        print(f"  IP地址:    {adapter_info.get('ip', '未配置')}")
        print(f"  子网掩码:  {adapter_info.get('mask', '未配置')}")
        print(f"  默认网关:  {adapter_info.get('gateway', '未配置')}")

        # 推荐测试IP
        suggested_ips = self.suggest_test_ip(adapter_info)

        if suggested_ips:
            print(f"\n💡 为您推荐的测试IP地址:")
            print("-" * 60)
            for i, ip in enumerate(suggested_ips, 1):
                print(f"{i}. {ip}")

            print(f"{len(suggested_ips) + 1}. 自定义IP地址")
            print(f"{len(suggested_ips) + 2}. 改回DHCP自动获取")

            try:
                ip_choice = input(f"\n请选择 (1-{len(suggested_ips) + 2}): ").strip()
                ip_choice_num = int(ip_choice)

                if ip_choice_num < 1 or ip_choice_num > len(suggested_ips) + 2:
                    print("❌ 无效选择")
                    return

                # 选择DHCP
                if ip_choice_num == len(suggested_ips) + 2:
                    print(f"\n🔄 将改为DHCP自动获取IP")
                    confirm = input("确认? (y/n): ").lower()
                    if confirm == 'y':
                        self.set_dhcp(selected_adapter)
                        self.test_network("www.baidu.com")
                    return

                # 选择推荐IP
                if ip_choice_num <= len(suggested_ips):
                    new_ip = suggested_ips[ip_choice_num - 1]
                else:
                    new_ip = input("请输入自定义IP地址: ").strip()

            except ValueError:
                print("❌ 输入无效")
                return
        else:
            new_ip = input("\n请输入新的IP地址: ").strip()

        # 使用当前配置的子网掩码和网关
        subnet_mask = adapter_info.get('mask', '255.255.255.0')
        gateway = adapter_info.get('gateway', None)

        # 确认设置
        print(f"\n{'='*60}")
        print(f"📝 即将设置:")
        print(f"{'='*60}")
        print(f"  网卡:      {selected_adapter}")
        print(f"  新IP:      {new_ip}")
        print(f"  子网掩码:  {subnet_mask}")
        print(f"  网关:      {gateway if gateway else '(自动)'}")

        confirm = input("\n✅ 确认设置? (y/n): ").lower()

        if confirm == 'y':
            # DNS设置
            use_default_dns = input("使用默认DNS (8.8.8.8 / 8.8.4.4)? (y/n): ").lower()
            if use_default_dns == 'y':
                dns1 = "8.8.8.8"
                dns2 = "8.8.4.4"
            else:
                dns1 = input("首选DNS (留空跳过): ").strip() or None
                dns2 = input("备用DNS (留空跳过): ").strip() or None

            # 执行设置
            success = self.set_static_ip(selected_adapter, new_ip, subnet_mask, gateway, dns1, dns2)

            if success:
                # 测试网络
                print("\n🌐 正在测试网络连接...")
                self.test_network("www.baidu.com")

                print("\n✅ 设置完成!")
                print(f"💡 如需改回原设置，请运行脚本选择 '改回DHCP' 或手动设置")
        else:
            print("❌ 已取消操作")

    def list_network_adapters(self):
        """列出所有网络适配器"""
        print("\n📡 网络适配器列表:")
        print("-" * 60)

        try:
            # 使用netsh命令列出网络适配器
            result = subprocess.run(
                ['netsh', 'interface', 'ip', 'show', 'config'],
                capture_output=True,
                text=True,
                encoding='gbk'
            )
            print(result.stdout)

            # 也可以使用ipconfig查看
            print("\n💻 详细网络信息 (ipconfig):")
            print("-" * 60)
            result2 = subprocess.run(
                ['ipconfig', '/all'],
                capture_output=True,
                text=True,
                encoding='gbk'
            )
            print(result2.stdout)

        except Exception as e:
            print(f"❌ 获取网络适配器失败: {e}")

    def set_static_ip(self, interface_name, ip_address, subnet_mask, gateway=None, dns1=None, dns2=None):
        """
        设置静态IP地址

        Args:
            interface_name: 网络适配器名称 (例如: "以太网", "WLAN")
            ip_address: IP地址 (例如: "192.168.1.100")
            subnet_mask: 子网掩码 (例如: "255.255.255.0")
            gateway: 网关 (例如: "192.168.1.1")
            dns1: 首选DNS (例如: "8.8.8.8")
            dns2: 备用DNS (例如: "8.8.4.4")
        """
        print(f"\n🔧 设置静态IP - 网卡: {interface_name}")

        try:
            # 设置IP地址
            cmd = [
                'netsh', 'interface', 'ip', 'set', 'address',
                f'name={interface_name}',
                'static',
                ip_address,
                subnet_mask
            ]

            if gateway:
                cmd.append(gateway)
                cmd.append('1')  # metric

            print(f"执行命令: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True, encoding='gbk')

            if result.returncode == 0:
                print(f"✅ IP地址设置成功: {ip_address}")
            else:
                print(f"❌ IP地址设置失败: {result.stderr}")
                return False

            # 设置DNS
            if dns1:
                self.set_dns(interface_name, dns1, dns2)

            return True

        except Exception as e:
            print(f"❌ 设置静态IP失败: {e}")
            return False

    def set_dhcp(self, interface_name):
        """
        设置为DHCP自动获取IP

        Args:
            interface_name: 网络适配器名称
        """
        print(f"\n🔄 设置DHCP - 网卡: {interface_name}")

        try:
            # 设置IP为DHCP
            cmd_ip = ['netsh', 'interface', 'ip', 'set', 'address', f'name={interface_name}', 'dhcp']
            result = subprocess.run(cmd_ip, capture_output=True, text=True, encoding='gbk')

            if result.returncode == 0:
                print("✅ IP地址已设置为DHCP")
            else:
                print(f"❌ 设置DHCP失败: {result.stderr}")
                return False

            # 设置DNS为DHCP
            cmd_dns = ['netsh', 'interface', 'ip', 'set', 'dns', f'name={interface_name}', 'dhcp']
            subprocess.run(cmd_dns, capture_output=True, text=True, encoding='gbk')
            print("✅ DNS已设置为DHCP")

            return True

        except Exception as e:
            print(f"❌ 设置DHCP失败: {e}")
            return False

    def set_dns(self, interface_name, dns1, dns2=None):
        """
        设置DNS服务器

        Args:
            interface_name: 网络适配器名称
            dns1: 首选DNS
            dns2: 备用DNS (可选)
        """
        try:
            # 设置首选DNS
            cmd1 = ['netsh', 'interface', 'ip', 'set', 'dns', f'name={interface_name}', 'static', dns1]
            result = subprocess.run(cmd1, capture_output=True, text=True, encoding='gbk')

            if result.returncode == 0:
                print(f"✅ 首选DNS设置成功: {dns1}")
            else:
                print(f"❌ DNS设置失败: {result.stderr}")
                return False

            # 设置备用DNS
            if dns2:
                cmd2 = ['netsh', 'interface', 'ip', 'add', 'dns', f'name={interface_name}', dns2, 'index=2']
                subprocess.run(cmd2, capture_output=True, text=True, encoding='gbk')
                print(f"✅ 备用DNS设置成功: {dns2}")

            return True

        except Exception as e:
            print(f"❌ 设置DNS失败: {e}")
            return False

    def test_network(self, host="8.8.8.8"):
        """
        测试网络连接

        Args:
            host: 要ping的主机地址
        """
        print(f"\n🌐 测试网络连接: {host}")

        try:
            result = subprocess.run(
                ['ping', '-n', '4', host],
                capture_output=True,
                text=True,
                encoding='gbk'
            )
            print(result.stdout)

            if result.returncode == 0:
                print("✅ 网络连接正常")
                return True
            else:
                print("❌ 网络连接失败")
                return False

        except Exception as e:
            print(f"❌ 测试失败: {e}")
            return False

    def release_renew_ip(self, interface_name=None):
        """
        释放并重新获取IP地址 (DHCP)

        Args:
            interface_name: 网络适配器名称 (可选,留空则对所有适配器操作)
        """
        print("\n🔄 释放并重新获取IP地址")

        try:
            if interface_name:
                subprocess.run(['ipconfig', '/release', interface_name], encoding='gbk')
                print(f"✅ 已释放IP: {interface_name}")
                subprocess.run(['ipconfig', '/renew', interface_name], encoding='gbk')
                print(f"✅ 已重新获取IP: {interface_name}")
            else:
                subprocess.run(['ipconfig', '/release'], encoding='gbk')
                print("✅ 已释放所有IP")
                subprocess.run(['ipconfig', '/renew'], encoding='gbk')
                print("✅ 已重新获取所有IP")

            return True

        except Exception as e:
            print(f"❌ 操作失败: {e}")
            return False

    def flush_dns(self):
        """刷新DNS缓存"""
        print("\n🔄 刷新DNS缓存")

        try:
            subprocess.run(['ipconfig', '/flushdns'], encoding='gbk')
            print("✅ DNS缓存已刷新")
            return True
        except Exception as e:
            print(f"❌ 刷新失败: {e}")
            return False


def main():
    """主函数 - 交互式菜单"""
    changer = NetworkIPChanger()

    while True:
        print("\n" + "="*60)
        print("🌐 网络IP修改工具 (测试用)")
        print("="*60)
        print("🎯 1. 智能推荐并设置IP (推荐!)")
        print("📡 2. 查看网络适配器")
        print("🔧 3. 手动设置静态IP")
        print("🔄 4. 设置DHCP (自动获取)")
        print("🌐 5. 仅设置DNS")
        print("🔄 6. 释放/重新获取IP")
        print("🗑️  7. 刷新DNS缓存")
        print("🌐 8. 测试网络连接")
        print("❌ 0. 退出")
        print("="*60)

        choice = input("\n请选择操作 (0-8): ").strip()

        if choice == '0':
            print("\n👋 再见!")
            break

        elif choice == '1':
            changer.smart_suggest_and_set()

        elif choice == '2':
            changer.list_network_adapters()

        elif choice == '3':
            interface = input("请输入网络适配器名称 (如: 以太网, WLAN): ").strip()
            ip = input("请输入IP地址 (如: 192.168.1.100): ").strip()
            mask = input("请输入子网掩码 (如: 255.255.255.0): ").strip()
            gateway = input("请输入网关 (留空跳过): ").strip() or None
            dns1 = input("请输入首选DNS (留空跳过): ").strip() or None
            dns2 = input("请输入备用DNS (留空跳过): ").strip() or None

            changer.set_static_ip(interface, ip, mask, gateway, dns1, dns2)

        elif choice == '4':
            interface = input("请输入网络适配器名称: ").strip()
            changer.set_dhcp(interface)

        elif choice == '5':
            interface = input("请输入网络适配器名称: ").strip()
            dns1 = input("请输入首选DNS: ").strip()
            dns2 = input("请输入备用DNS (留空跳过): ").strip() or None
            changer.set_dns(interface, dns1, dns2)

        elif choice == '6':
            interface = input("请输入网络适配器名称 (留空为所有): ").strip() or None
            changer.release_renew_ip(interface)

        elif choice == '7':
            changer.flush_dns()

        elif choice == '8':
            host = input("请输入要测试的主机 (留空为www.baidu.com): ").strip() or "www.baidu.com"
            changer.test_network(host)

        else:
            print("❌ 无效选择,请重新输入")


if __name__ == "__main__":
    print("\n" + "="*60)
    print("🚀 网络IP修改工具启动中...")
    print("="*60)
    print("⚠️  注意: 需要管理员权限才能修改网络配置")
    print("⚠️  仅用于测试目的")
    print("💡 推荐: 使用选项1【智能推荐】,自动分析并推荐合适的IP")

    main()
