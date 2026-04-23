import os
import re
import time
import urllib.parse
import requests
from bs4 import BeautifulSoup
from datetime import datetime

# ============================================================
#  财经早报 · 必应新闻搜索 · 飞书推送 (稳定版)
# ============================================================

WEBHOOK_URL = os.environ.get("FEISHU_WEBHOOK")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

seen_titles = set()


# ===================== 工具函数 =====================

def safe_request(url, timeout=8):
    """安全 HTTP GET，8 秒超时"""
    try:
        res = requests.get(url, headers=HEADERS, timeout=timeout)
        res.raise_for_status()
        res.encoding = "utf-8"
        return res.text
    except Exception as e:
        print(f"    ⚠ 请求异常: {e}")
        return None


def extract_real_url(raw_url):
    """纯本地字符串解析提取真实 URL，零网络请求"""
    if not raw_url or "bing.com" not in raw_url:
        return raw_url
    try:
        decoded = raw_url
        for _ in range(3):
            new = urllib.parse.unquote(decoded)
            if new == decoded:
                break
            decoded = new
        for pattern in [r"[?&]u=(https?://[^&\s]+)", r"[?&]U=(https?://[^&\s]+)"]:
            m = re.search(pattern, decoded)
            if m:
                return m.group(1)
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

def fetch_bing(domain, source_name, extra_keyword=""):
    """通过必应精准搜索获取新闻，仅 1 次 HTTP 请求"""
    t0 = time.time()
    print(f"  ⏳ [{source_name}] 正在搜索...")

    # 使用上次验证可用的搜索格式，不加 mkt 参数
    # 如果传了 extra_keyword，则追加到搜索词中
    if extra_keyword:
        q = f"site%3A{domain}+{extra_keyword}"
    else:
        q = f"site%3A{domain}"
    search_url = f"https://www.bing.com/news/search?q={q}&form=PTFTNR"

    html = safe_request(search_url, timeout=8)
    elapsed = time.time() - t0

    if not html:
        print(f"  ❌ [{source_name}] 请求失败 ({elapsed:.1f}s)")
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
        print(f"  ✅ [{source_name}] 成功 {len(result)} 条 ({elapsed:.1f}s)")
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
        print("❌ 缺少环境变量 FEISHU_WEBHOOK")
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

    # (name, domain, extra_keyword)
    # 绝大多数不加额外关键词，保持上次验证可用的格式
    # 中国经济网：单独加一个中文财经关键词，引导必应返回中文结果
    sources = [
        ("第一财经",       "yicai.com",       ""),
        ("财新网",         "caixin.com",       ""),
        ("东方财富网",     "eastmoney.com",    ""),
        ("华尔街见闻",     "wallstreetcn.com", ""),
        ("21世纪经济报道", "21jingji.com",     ""),
        ("中国经济网",     "ce.cn",            "财经"),
    ]

    all_news = {}
    for name, domain, extra_kw in sources:
        if time.time() - total_t0 > 50:
            print(f"\n⏰ 全局超时保护，跳过 [{name}]，直接发送。")
            break
        all_news[name] = fetch_bing(domain, name, extra_kw)
        time.sleep(0.5)

    msg = build_message(all_news)
    print("\n" + "-" * 50)
    print("📋 早报内容预览:")
    print("-" * 50)
    print(msg)
    send_feishu(msg)
    print(f"\n⏱ 总耗时: {time.time() - total_t0:.1f} 秒")


if __name__ == "__main__":
    main()
