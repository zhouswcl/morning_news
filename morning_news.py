import os
import re
import time
import random
import hashlib
import urllib.parse
import requests
from bs4 import BeautifulSoup
from datetime import datetime

# ============================================================
#  财经早报 · 混合策略（必应新闻 + RSS/页面直抓） · 飞书推送
# ============================================================

WEBHOOK_URL = os.environ.get("FEISHU_WEBHOOK")
BAIDU_APP_ID = os.environ.get("BAIDU_APP_ID")
BAIDU_SECRET_KEY = os.environ.get("BAIDU_SECRET_KEY")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

seen_titles = set()
TRANSLATE_SOURCES = {"彭博社", "路透社"}  # 若需要翻译，可以扩展


# ===================== 翻译与工具函数 =====================

def is_chinese(text):
    return bool(re.search(r'[\u4e00-\u9fa5]', text))


def translate_to_chinese(text):
    """百度翻译：仅对英文翻译；若已含中文或未配置 Key 则直接返回原文"""
    if not text or not BAIDU_APP_ID or not BAIDU_SECRET_KEY or is_chinese(text):
        return text
    try:
        salt = str(random.randint(32768, 65536))
        sign_str = BAIDU_APP_ID + text + salt + BAIDU_SECRET_KEY
        sign = hashlib.md5(sign_str.encode('utf-8')).hexdigest()
        url = "https://fanyi-api.baidu.com/api/trans/vip/translate"
        params = {
            "q": text,
            "from": "en",
            "to": "zh",
            "appid": BAIDU_APP_ID,
            "salt": salt,
            "sign": sign
        }
        res = requests.get(url, params=params, timeout=5)
        res_json = res.json()
        if "trans_result" in res_json:
            dst = res_json["trans_result"][0]["dst"]
            print(f"    🌐 翻译: {text[:30]}... → {dst[:30]}...")
            return dst
    except Exception as e:
        print(f"    ⚠ 翻译异常: {e}")
    return text


def safe_request(url, timeout=8):
    try:
        res = requests.get(url, headers=HEADERS, timeout=timeout)
        res.raise_for_status()
        res.encoding = "utf-8"
        return res.text
    except Exception as e:
        print(f"    ⚠ 请求失败: {e}")
        return None


def extract_real_url(raw_url):
    """从必应跳转链接提取真实 URL（纯本地字符串解析，零网络请求）"""
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
            "xueqiu.com", "bloomberg.com", "cn.reuters.com", "reuters.com",
            "wind.com.cn", "jrj.com.cn", "finance.jrj.com.cn",
            "businessweekchina.com",
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
        if len(t) < 6 or t in seen_titles:
            continue
        seen_titles.add(t)
        result.append(it)
        if len(result) >= limit:
            break
    return result


# ===================== 必应新闻搜索（保持稳定，只给收录好的源用） =====================

def fetch_bing_news(domain, source_name, extra_keyword=""):
    """必应新闻搜索：仅用于已经验证收录良好的媒体"""
    t0 = time.time()
    print(f"  ⏳ [{source_name}] 必应新闻搜索...")
    q = f"site%3A{domain}+{extra_keyword}" if extra_keyword else f"site%3A{domain}"
    search_url = f"https://www.bing.com/news/search?q={q}&form=PTFTNR"
    html = safe_request(search_url, timeout=8)
    elapsed = time.time() - t0
    if not html:
        print(f"  ❌ [{source_name}] 请求失败 ({elapsed:.1f}s)")
        return []
    try:
        soup = BeautifulSoup(html, "html.parser")
        items = [{"title": a.get_text(strip=True), "url": extract_real_url(a.get("href", ""))}
                 for a in soup.select("a.title") if a.get_text(strip=True) and a.get("href")]
        result = dedup(items)
        print(f"  ✅ [{source_name}] 成功 {len(result)} 条 ({elapsed:.1f}s)")
        return result
    except Exception as e:
        print(f"  ❌ [{source_name}] 解析失败 ({elapsed:.1f}s) - {e}")
        return []


# ===================== RSS 通用解析 =====================

def fetch_rss(rss_url, source_name, limit=5):
    """通用 RSS 解析（RSS/Atom 均兼容）"""
    t0 = time.time()
    print(f"  ⏳ [{source_name}] 抓取 RSS: {rss_url}")
    xml = safe_request(rss_url, timeout=10)
    elapsed = time.time() - t0
    if not xml:
        print(f"  ❌ [{source_name}] RSS 请求失败 ({elapsed:.1f}s)")
        return []
    try:
        soup = BeautifulSoup(xml, "html.parser")
        items = []
        # RSS 2.0：channel/item
        for item in soup.select("item"):
            title_el = item.find("title")
            link_el = item.find("link")
            title = title_el.get_text(strip=True) if title_el else None
            link = link_el.get_text(strip=True) if link_el else None
            if title and link:
                items.append({"title": title, "url": link})
        # Atom：feed/entry
        if not items:
            for entry in soup.select("entry"):
                title_el = entry.find("title")
                link_el = entry.find("link", href=True)
                title = title_el.get_text(strip=True) if title_el else None
                link = link_el["href"] if link_el and link_el.get("href") else None
                if title and link:
                    items.append({"title": title, "url": link})
        result = dedup(items, limit=limit)
        print(f"  ✅ [{source_name}] 成功 {len(result)} 条 ({elapsed:.1f}s)")
        return result
    except Exception as e:
        print(f"  ❌ [{source_name}] RSS 解析失败 ({elapsed:.1f}s) - {e}")
        return []


# ===================== 直接页面抓取（金融界移动端列表） =====================

def fetch_jrj_list(list_url, source_name, limit=5):
    """
    直接抓取金融界移动端列表页（例如：m.jrj.com.cn/list/103/）
    页面为静态 HTML，包含多条新闻，每条为一个 <li>，内部有 <a>
    """
    t0 = time.time()
    print(f"  ⏳ [{source_name}] 抓取页面: {list_url}")
    html = safe_request(list_url, timeout=10)
    elapsed = time.time() - t0
    if not html:
        print(f"  ❌ [{source_name}] 页面请求失败 ({elapsed:.1f}s)")
        return []
    try:
        soup = BeautifulSoup(html, "html.parser")
        items = []
        # 金融界移动端列表条目通常在 .list li / .item li 等结构中
        for li in soup.select("li"):
            a = li.find("a", href=True)
            if not a:
                continue
            title = a.get_text(strip=True)
            href = a.get("href", "")
            if not title or not href:
                continue
            # 相对路径补全
            if not re.match(r"^https?://", href):
                href = urllib.parse.urljoin(list_url, href)
            items.append({"title": title, "url": href})
        result = dedup(items, limit=limit)
        print(f"  ✅ [{source_name}] 成功 {len(result)} 条 ({elapsed:.1f}s)")
        return result
    except Exception as e:
        print(f"  ❌ [{source_name}] 页面解析失败 ({elapsed:.1f}s) - {e}")
        return []


# ===================== 格式化 & 推送 =====================

def build_message(all_news):
    now = datetime.now()
    wd = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"][now.weekday()]
    date_str = now.strftime("%Y年%m月%d日")
    text = f"📰 财经早报 | {date_str} {wd}\n\n"
    for source, news_list in all_news.items():
        text += f"【{source}】\n"
        if news_list:
            for i, item in enumerate(news_list, 1):
                text += f"{i}. {item['title']}\n   {item['url']}\n"
        else:
            text += "（暂未抓取到最新内容）\n"
        text += "\n"
    return text


def send_feishu(text):
    if not WEBHOOK_URL:
        print("❌ 缺少环境变量 FEISHU_WEBHOOK")
        return False
    try:
        requests.post(WEBHOOK_URL, json={"msg_type": "text", "content": {"text": text}}, timeout=15).raise_for_status()
        print("✅ 飞书推送成功")
        return True
    except Exception as e:
        print(f"❌ 推送失败: {e}")
        return False


# ===================== 主程序 =====================

def main():
    total_t0 = time.time()
    print("=" * 50)
    print(f"📰 财经早报任务启动 {datetime.now().strftime('%H:%M:%S')}")
    print("=" * 50)

    all_news = {}

    # 1) 必应新闻搜索（稳定收录）
    bing_sources = [
        ("第一财经",       "yicai.com",               ""),
        ("财新网",         "caixin.com",              ""),
        ("东方财富网",     "eastmoney.com",           ""),
        ("华尔街见闻",     "wallstreetcn.com",        ""),
        ("21世纪经济报道", "21jingji.com",            ""),
        ("中国经济网",     "ce.cn",                   "财经"),
    ]
    for name, domain, kw in bing_sources:
        if time.time() - total_t0 > 100:
            print(f"⏰ 超时保护，跳过 [{name}]")
            break
        all_news[name] = fetch_bing_news(domain, name, kw)
        time.sleep(0.5)

    # 2) RSS 直抓（雪球、路透、彭博中文聚合、万得替代：财联社 7x24 RSS）
    rss_sources = [
        ("雪球·今日话题", "https://xueqiu.com/hots/topic/rss", 5),
        ("路透社", "http://cn.reuters.com/rssFeed/CNIntlBizNews/", 5),
        ("彭博社", "https://bbg.buzzing.cc/rss", 5),  # bbg.buzzing.cc 的“Atom/RSS Feed”
        ("财联社·电报", "https://www.cls.cn/api/telegraph/rss", 5),
    ]
    for name, url, limit in rss_sources:
        if time.time() - total_t0 > 100:
            print(f"⏰ 超时保护，跳过 [{name}]")
            break
        all_news[name] = fetch_rss(url, name, limit=limit)
        time.sleep(0.5)

    # 3) 金融界：直接抓取移动端“财经”列表页
    if time.time() - total_t0 <= 100:
        all_news["金融界"] = fetch_jrj_list("https://m.jrj.com.cn/list/103/", "金融界", limit=5)

    # 4) 万得：目前无稳定公开 RSS/页面，先用 RSS 聚合（财联社·电报）替代；若后续有官方 RSS，可替换
    # all_news["万得资讯"] = []  # 保持空或注释掉即可

    # 5) 翻译（仅对 TRANSLATE_SOURCES 中非中文标题）
    for src in TRANSLATE_SOURCES:
        if src in all_news:
            for item in all_news[src]:
                if not is_chinese(item.get("title", "")):
                    item["title"] = translate_to_chinese(item["title"])
                    time.sleep(0.3)

    msg = build_message(all_news)
    print("\n" + "-" * 50)
    print("📋 早报内容预览:")
    print("-" * 50)
    print(msg)
    send_feishu(msg)
    print(f"\n⏱ 总耗时: {time.time() - total_t0:.1f} 秒")


if __name__ == "__main__":
    main()
