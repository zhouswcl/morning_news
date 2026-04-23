import os
import re
import time
import urllib.parse
import requests
from bs4 import BeautifulSoup
from datetime import datetime

# ============================================================
#  财经早报 · 必应新闻搜索 · 飞书推送 (极速稳定版)
# ============================================================

WEBHOOK_URL = os.environ.get("FEISHU_WEBHOOK")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# 全局去重
seen_titles = set()


# ===================== 工具函数 =====================

def safe_request(url, timeout=8):
    """安全 HTTP GET (严格控制 8 秒超时，防止卡死)"""
    try:
        res = requests.get(url, headers=HEADERS, timeout=timeout)
        res.raise_for_status()
        res.encoding = "utf-8"
        return res.text
    except Exception as e:
        print(f"    ⚠ 请求异常: {e}")
        return None


def extract_real_url(raw_url):
    """纯本地字符串解析，从必应跳转链接中提取真实新闻 URL，零网络请求"""
    if not raw_url or "bing.com" not in raw_url:
        return raw_url

    try:
        decoded = raw_url
        for _ in range(3):
            new = urllib.parse.unquote(decoded)
            if new == decoded:
                break
            decoded = new

        # 策略1: 提取 u= 或 U= 参数
        for pattern in [r"[?&]u=(https?://[^&\s]+)", r"[?&]U=(https?://[^&\s]+)"]:
            m = re.search(pattern, decoded)
            if m:
                return m.group(1)

        # 策略2: 文本中搜索目标媒体域名
        target_domains = [
            "yicai.com", "caixin.com", "eastmoney.com",
            "wallstreetcn.com", "21jingji.com", "finance.ce.cn", "ce.cn"
        ]
        for d in target_domains:
            m = re.search(rf"(https?://[^&\s]*{re.escape(d)}[^\s&]*)", decoded)
            if m:
                return m.group(1)
    except Exception:
        pass

    return raw_url


def dedup(items, limit=5):
    """标题去重 + 截断"""
    result = []
    for it in items:
        t = re.sub(r"\s+", "", it.get("title", ""))
        if len(t) < 8 or t in seen_titles:
            continue
        seen_titles.add(t)
        result.append(it)
        if len(result) >= limit:
            break
    return result


# ===================== 核心抓取 =====================

def fetch_bing(domain, source_name):
    """通过必应精准搜索获取新闻"""
    t0 = time.time()
    print(f"  ⏳ [{source_name}] 正在搜索...")

    # 核心修改1：强制加上 mkt=zh-CN&setlang=zh-CN，逼迫必应从美国IP也返回中文结果
    search_url = (
        f"https://www.bing.com/news/search"
        f"?q=site%3A{domain}&form=PTFTNR&mkt=zh-CN&setlang=zh-CN"
    )

    html = safe_request(search_url, timeout=8)
    elapsed = time.time() - t0

    if not html:
        print(f"  ❌ [{source_name}] 请求超时或失败 ({elapsed:.1f}s)")
        return []

    try:
        soup = BeautifulSoup(html, "html.parser")
        items = []
        for a in soup.select("a.title"):
            title = a.get_text(strip=True)
            href = a.get("href", "")
            if title and href:
                real_url = extract_real_url(href)
                items.append({"title": title, "url": real_url})

        result = dedup(items)
        print(f"  ✅ [{source_name}] 成功获取 {len(result)} 条 ({elapsed:.1f}s)")
        return result
    except Exception as e:
        print(f"  ❌ [{source_name}] 解析失败 ({elapsed:.1f}s) - {e}")
        return []


# ===================== 格式化 & 推送 =====================

def build_message(all_news):
    now = datetime.now()
    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    wd = weekdays[now.weekday()]
    date_str = now.strftime("%Y年%m月%d日")

    text = f"📰 财经早报 | {date_str} {wd}\n\n"

    has_any = False
    for source, news_list in all_news.items():
        if not news_list:
            continue
        has_any = True
        text += f"【{source}】\n"
        for i, item in enumerate(news_list, 1):
            text += f"{i}. {item['title']}\n   {item['url']}\n"
        text += "\n"

    if not has_any:
        text += "⚠️ 所有媒体源抓取均失败，请稍后重试。"

    return text


def send_feishu(text):
    if not WEBHOOK_URL:
        print("❌ 错误: 缺少环境变量 FEISHU_WEBHOOK")
        return False
    try:
        payload = {"msg_type": "text", "content": {"text": text}}
        res = requests.post(WEBHOOK_URL, json=payload, timeout=15)
        res.raise_for_status()
        print("✅ 飞书推送成功")
        return True
    except Exception as e:
        print(f"❌ 飞书推送失败: {e}")
        return False


# ===================== 主程序 =====================

def main():
    total_t0 = time.time()
    print("=" * 50)
    print(f"📰 财经早报任务启动 {datetime.now().strftime('%H:%M:%S')}")
    print("=" * 50)

    # 核心修改2：中国经济网使用具体的中文财经子频道，彻底屏蔽英文 en.ce.cn
    sources = {
        "第一财经":       "yicai.com",
        "财新网":         "caixin.com",
        "东方财富网":     "eastmoney.com",
        "华尔街见闻":     "wallstreetcn.com",
        "21世纪经济报道": "21jingji.com",
        "中国经济网":     "finance.ce.cn",  
    }

    all_news = {}
    for name, domain in sources.items():
        # 核心修改3：增加全局防卡死机制。如果总耗时超过 50 秒，直接放弃剩余源，立刻发报
        if time.time() - total_t0 > 50:
            print(f"\n⏰ 全局超时保护触发，跳过 [{name}]，直接发送已有内容。")
            break
            
        all_news[name] = fetch_bing(domain, name)
        time.sleep(0.5)  # 缩短源间延迟至 0.5 秒，加快整体速度

    msg = build_message(all_news)

    print("\n" + "-" * 50)
    print("📋 早报内容预览:")
    print("-" * 50)
    print(msg)

    send_feishu(msg)

    total_sec = time.time() - total_t0
    print(f"\n⏱ 任务结束，总耗时: {total_sec:.1f} 秒")


if __name__ == "__main__":
    main()
