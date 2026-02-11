"""
钉钉机器人推送模块
"""

import json
import time
import hmac
import hashlib
import base64
import urllib.request
import urllib.parse
import ssl
import logging

logger = logging.getLogger(__name__)

ssl._create_default_https_context = ssl._create_unverified_context


def _make_sign(secret: str) -> tuple:
    """生成钉钉加签参数"""
    timestamp = str(round(time.time() * 1000))
    string_to_sign = f'{timestamp}\n{secret}'
    hmac_code = hmac.new(
        secret.encode('utf-8'),
        string_to_sign.encode('utf-8'),
        digestmod=hashlib.sha256
    ).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
    return timestamp, sign


def send_dingtalk(webhook: str, secret: str, title: str, content: str) -> bool:
    """
    通过钉钉机器人发送Markdown消息

    Args:
        webhook: Webhook URL
        secret: 加签密钥
        title: 消息标题
        content: 消息内容（Markdown格式）

    Returns:
        是否发送成功
    """
    if not webhook or not secret:
        logger.warning("钉钉 Webhook 或 Secret 未配置，跳过推送")
        return False

    timestamp, sign = _make_sign(secret)
    url = f"{webhook}&timestamp={timestamp}&sign={sign}"

    data = json.dumps({
        "msgtype": "markdown",
        "markdown": {
            "title": title,
            "text": content,
        }
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if result.get("errcode") == 0:
                logger.info(f"钉钉推送成功: {title}")
                return True
            else:
                logger.error(f"钉钉推送失败: {result.get('errmsg', '未知错误')}")
                return False
    except Exception as e:
        logger.error(f"钉钉推送异常: {e}")
        return False


def format_signal_message(period_name: str, normal_results: list, strict_results: list) -> str:
    """
    将选股结果格式化为Markdown消息

    Args:
        period_name: 周期名称
        normal_results: [(code, name, details), ...]
        strict_results: [(code, name, details), ...]

    Returns:
        Markdown格式消息
    """
    lines = [f"## 📊 {period_name} 选股信号\n"]

    if strict_results:
        lines.append("### 🔴 严格买入信号\n")
        lines.append("| 代码 | 名称 | 收盘价 | 信号日期 | 金叉日期 |")
        lines.append("|------|------|--------|----------|----------|")
        for code, name, d in strict_results:
            lines.append(
                f"| {code} | {name} | {d.get('close', 0):.2f} "
                f"| {d.get('date', '')} | {d.get('gold_cross_date', '')} |"
            )
        lines.append("")

    if normal_results:
        lines.append("### 🟡 普通买入信号\n")
        lines.append("| 代码 | 名称 | 收盘价 | 信号日期 | 金叉日期 |")
        lines.append("|------|------|--------|----------|----------|")
        for code, name, d in normal_results:
            lines.append(
                f"| {code} | {name} | {d.get('close', 0):.2f} "
                f"| {d.get('date', '')} | {d.get('gold_cross_date', '')} |"
            )
        lines.append("")

    total = len(normal_results) + len(strict_results)
    lines.append(f"**共 {total} 只** (严格 {len(strict_results)} + 普通 {len(normal_results)})")

    return "\n".join(lines)
