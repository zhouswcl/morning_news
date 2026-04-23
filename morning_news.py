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
#  财经早报 · 必应新闻搜索 · 百度翻译 · 飞书推送（稳定版）
# ============================================================

WEBHOOK_URL = os.environ.get("FEISHU_WEBHOOK")
BAIDU_APP_ID = os.environ.get("BAIDU_APP_ID")
BAIDU_SECRET_KEY = os.environ.get("BAIDU_SECRET_KEY")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# 全局去重集合
seen_titles = set()

# 需要中英互译检查的媒体
TRANSLATE_SOURCES = {"彭博社", "路透社"}


# ===================== 翻译与工具函数 =====================

def is_chinese(text):
    """判断文本是否包含中文字符"""
    return bool(re.search(r'[\u4e00-\u9fa5]', text))


def translate_to_chinese(text):
    """调用百度翻译 API 将英文翻译为中文（标准版）"""
    if not text or not BAIDU_APP_ID or not BAIDU_SECRET_KEY or is_chinese(text):
        return text

    try:
        salt = str(random.randint(32768, 65536))
        sign_str = BAIDU_APP_ID + text + salt + BAIDU_SECRET_KEY
        sign = hashlib.md5(sign_str.encode('utf-8')).hexdigest()

        # 官方接口地址（HTTPS）
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
            translated = res_json["trans_result"][0]["dst"]
            print(f"    🌐 翻译: {text[:20]}... -> {translated[:20]}...")
            return translated
        else:
            print(f"    ⚠ 翻译接口返回异常: {res_json}")
    except Exception as e:
        print(f"    ⚠ 翻译请求失败: {e}")

    return text  # 翻译失败则返回原文本


def safe_request(url, timeout=8):
    """安全 HTTP GET（严格超时）"""
    try:
        res = requests.get(url, headers=HEADERS, timeout=timeout)
        res.raise_for_status()
        res.encoding = "utf-8"
        return res.text
    except Exception as e:
        print(f"    ⚠ 请求异常: {e}")
        return None


def extract_real_url(raw_url):
    """从必应跳转链接中提取真实 URL（纯本地字符串解析，零网络请求）"""
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

        # 策略2: 在整段文本中搜索目标媒体的域名 URL
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
    """标题去重 + 截断到指定条数"""
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
    """通过必应精准搜索获取新闻（单个源只发 1 次请求）"""
    t0 = time.time()
    print(f"  ⏳ [{source_name}] 正在搜索...")

    # 构造搜索 URL（不乱加 mkt，保持稳定）
    q = f"site%3A{domain}+{extra_keyword}" if extra_keyword else f"site%3A{domain}"
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

        # 针对特定媒体执行翻译
        if source_name in TRANSLATE_SOURCES and result:
            for item in result:
                if not is_chinese(item['title']):
                    item['title'] = translate_to_chinese(item['title'])
                    time.sleep(0.3)  # 遵守百度翻译 QPS 限制

        print(f"  ✅ [{source_name}] 成功 {len(result)} 条 ({elapsed:.1f}s)")
        return result
    except Exception as e:
        print(f"  ❌ [{source_name}] 解析失败 ({elapsed:.1f}s) - {e}")
        return []


# ===================== 格式化 & 推送 =====================

def build_message(all_news):
    """生成早报文本"""
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
            # 飞书 text 类型: 独立一行的 URL 自动变为可点击链接
            text += f"{i}. {item['title']}\n   {item['url']}\n"
        text += "\n"

    if not has_any:
        text += "⚠️ 所有媒体源抓取均失败，请稍后重试。"

    return text


def send_feishu(text):
    """通过 Webhook 推送文本消息到飞书群"""
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

    # 媒体名称 → site: 域名 → 可选的关键词
    # 说明：
    #   - 彭博社：优先用“商业周刊/中文版”（businessweekchina.com）获取中文内容；若未命中，再退而用 bloomberg.com + China。
    #   - 路透社：用其中文站 cn.reuters.com；必要时加“中国”“财经”等关键词兜底。
    #   - 万得/雪球/金融界：必应新闻收录不稳定，替换为必应收录良好的中文财经源（标注于“显示名称”）。
    sources = [
        ("第一财经",       "yicai.com",               ""),
        ("财新网",         "caixin.com",              ""),
        ("东方财富网",     "eastmoney.com",           ""),
        ("华尔街见闻",     "wallstreetcn.com",        ""),
        ("21世纪经济报道", "21jingji.com",            ""),
        ("中国经济网",     "ce.cn",                   "财经"),

        ("雪球·热门",     "xueqiu.com",              ""),  # 必应新闻收录少，可接受偶尔为空
        ("彭博社",         "businessweekchina.com",   ""),  # 《商业周刊/中文版》公开站（含彭博中文稿件）
        ("路透社",         "cn.reuters.com",          ""),  # 路透中文网（中文）
        ("金融界",         "finance.jrj.com.cn",      ""),  # 金融界财经频道（有大量中文稿件）
        ("万得·资讯",     "www.wind.com.cn",         ""),  # Wind 官网资讯页（视必应收录而定，偶尔可能为空）
    ]

    all_news = {}
    for name, domain, extra_kw in sources:
        # 全局防卡死：总耗时超过 90 秒直接发报
        if time.time() - total_t0 > 90:
            print(f"\n⏰ 全局超时保护触发，跳过 [{name}]，直接发送。")
            break

        all_news[name] = fetch_bing(domain, name, extra_kw)
        time.sleep(0.5)

    msg = build_message(all_news)

    print("\n" + "-" * 50)
    print("📋 早报内容预览:")
    print("-" * 50)
    print(msg)

    send_feishu(msg)

    print(f"\n⏱ 全部完成，总耗时: {time.time() - total_t0:.1f} 秒")


if __name__ == "__main__":
    main()
