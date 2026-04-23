import os
import re
import time
import random
import hashlib
import urllib.parse
import requests
from bs4 import BeautifulSoup
from datetime import datetime

WEBHOOK_URL = os.environ.get("FEISHU_WEBHOOK")
BAIDU_APP_ID = os.environ.get("BAIDU_APP_ID")
BAIDU_SECRET_KEY = os.environ.get("BAIDU_SECRET_KEY")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

seen_titles = set()
TRANSLATE_SOURCES = {"彭博社", "路透社"}

def is_chinese(text):
    return bool(re.search(r'[\u4e00-\u9fa5]', text))

def translate_to_chinese(text):
    if not text or not BAIDU_APP_ID or not BAIDU_SECRET_KEY or is_chinese(text):
        return text
    try:
        salt = str(random.randint(32768, 65536))
        sign_str = BAIDU_APP_ID + text + salt + BAIDU_SECRET_KEY
        sign = hashlib.md5(sign_str.encode('utf-8')).hexdigest()
        url = "https://fanyi-api.baidu.com/api/trans/vip/translate"
        params = {"q": text, "from": "en", "to": "zh", "appid": BAIDU_APP_ID, "salt": salt, "sign": sign}
        res = requests.get(url, params=params, timeout=5)
        res_json = res.json()
        if "trans_result" in res_json:
            return res_json["trans_result"][0]["dst"]
    except Exception as e:
        print(f"    ⚠ 翻译失败: {e}")
    return text

def safe_request(url, timeout=8):
    try:
        res = requests.get(url, headers=HEADERS, timeout=timeout)
        res.raise_for_status()
        res.encoding = "utf-8"
        return res.text
    except Exception as e:
        return None

def extract_real_url(raw_url):
    if not raw_url or "bing.com" not in raw_url: return raw_url
    try:
        decoded = raw_url
        for _ in range(3):
            new = urllib.parse.unquote(decoded)
            if new == decoded: break
            decoded = new
        for pattern in [r"[?&]u=(https?://[^&\s]+)", r"[?&]U=(https?://[^&\s]+)"]:
            m = re.search(pattern, decoded)
            if m: return m.group(1)
        target_domains = ["xueqiu.com", "bloomberg.com", "cn.reuters.com", "reuters.com", "wind.com.cn", "jrj.com.cn", "finance.jrj.com.cn", "businessweekchina.com", "yicai.com", "caixin.com", "eastmoney.com", "wallstreetcn.com", "21jingji.com", "finance.ce.cn", "ce.cn"]
        for d in target_domains:
            m = re.search(rf"(https?://[^&\s]*{re.escape(d)}[^\s&]*)", decoded)
            if m: return m.group(1)
    except Exception: pass
    return raw_url

def dedup(items, limit=5):
    result = []
    for it in items:
        t = re.sub(r"\s+", "", it.get("title", ""))
        if len(t) < 8 or t in seen_titles: continue
        seen_titles.add(t)
        result.append(it)
        if len(result) >= limit: break
    return result

def fetch_bing_news(domain, source_name, extra_keyword=""):
    """专为必应新闻库收录良好的媒体设计"""
    t0 = time.time()
    print(f"  ⏳ [{source_name}] 新闻搜索...")
    q = f"site%3A{domain}+{extra_keyword}" if extra_keyword else f"site%3A{domain}"
    html = safe_request(f"https://www.bing.com/news/search?q={q}&form=PTFTNR", timeout=8)
    elapsed = time.time() - t0
    if not html:
        print(f"  ❌ [{source_name}] 请求失败 ({elapsed:.1f}s)")
        return []
    try:
        soup = BeautifulSoup(html, "html.parser")
        items = [{"title": a.get_text(strip=True), "url": extract_real_url(a.get("href", ""))} for a in soup.select("a.title") if a.get_text(strip=True) and a.get("href")]
        result = dedup(items)
        if source_name in TRANSLATE_SOURCES and result:
            for item in result:
                if not is_chinese(item['title']):
                    item['title'] = translate_to_chinese(item['title'])
                    time.sleep(0.3)
        print(f"  ✅ [{source_name}] 成功 {len(result)} 条 ({elapsed:.1f}s)")
        return result
    except Exception as e:
        print(f"  ❌ [{source_name}] 解析失败 ({elapsed:.1f}s)")
        return []

def fetch_bing_web(domain, source_name, extra_keyword=""):
    """为未被必应新闻库收录的媒体设计：搜全站网页，限制为近1天(qdr:d)"""
    t0 = time.time()
    print(f"  ⏳ [{source_name}] 网页搜索...")
    q = f"site:{domain} {extra_keyword}"
    # qdr:d 表示限制搜索时间为过去 1 天
    html = safe_request(f"https://www.bing.com/search?q={q}&qdr:d&form=PTFT18", timeout=8)
    elapsed = time.time() - t0
    if not html:
        print(f"  ❌ [{source_name}] 请求失败 ({elapsed:.1f}s)")
        return []
    try:
        soup = BeautifulSoup(html, "html.parser")
        items = []
        # 必应普通网页搜索的标题在 <li class="b_algo"><h2><a> 结构中
        for h2 in soup.select("li.b_algo h2"):
            a = h2.find("a", href=True)
            if a:
                title = a.get_text(strip=True)
                href = a["href"]
                if title and href:
                    items.append({"title": title, "url": extract_real_url(href)})
        result = dedup(items)
        if source_name in TRANSLATE_SOURCES and result:
            for item in result:
                if not is_chinese(item['title']):
                    item['title'] = translate_to_chinese(item['title'])
                    time.sleep(0.3)
        print(f"  ✅ [{source_name}] 成功 {len(result)} 条 ({elapsed:.1f}s)")
        return result
    except Exception as e:
        print(f"  ❌ [{source_name}] 解析失败 ({elapsed:.1f}s)")
        return []

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
            # 关键修改：即使 0 条，也要显示出来，避免“看起来没变”的错觉
            text += "（暂未抓取到最新内容）\n"
        text += "\n"
    return text

def send_feishu(text):
    if not WEBHOOK_URL: return False
    try:
        requests.post(WEBHOOK_URL, json={"msg_type": "text", "content": {"text": text}}, timeout=15).raise_for_status()
        print("✅ 飞书推送成功")
        return True
    except Exception as e:
        print(f"❌ 推送失败: {e}")
        return False

def main():
    total_t0 = time.time()
    print("📰 任务启动...")
    
    # 格式：(名称, 域名, 关键词, 搜索类型)
    # type='news' 用新闻库；type='web' 用普通网页库（针对雪球/彭博等非标准新闻源）
    sources = [
        ("第一财经",       "yicai.com",               "", "news"),
        ("财新网",         "caixin.com",              "", "news"),
        ("东方财富网",     "eastmoney.com",           "", "news"),
        ("华尔街见闻",     "wallstreetcn.com",        "", "news"),
        ("21世纪经济报道", "21jingji.com",            "", "news"),
        ("中国经济网",     "ce.cn",                   "财经", "news"),
        
        ("雪球",           "xueqiu.com",              "热点", "web"),
        ("彭博社",         "bloomberg.com",           "China", "web"),
        ("路透社",         "cn.reuters.com",          "", "web"),
        ("金融界",         "finance.jrj.com.cn",      "", "web"),
        ("万得资讯",       "www.wind.com.cn",         "新闻", "web"),
    ]

    all_news = {}
    for name, domain, extra_kw, search_type in sources:
        if time.time() - total_t0 > 100: break
        
        if search_type == "web":
            all_news[name] = fetch_bing_web(domain, name, extra_kw)
        else:
            all_news[name] = fetch_bing_news(domain, name, extra_kw)
        time.sleep(0.5)

    msg = build_message(all_news)
    print("\n" + msg)
    send_feishu(msg)
    print(f"⏱ 总耗时: {time.time() - total_t0:.1f} 秒")

if __name__ == "__main__":
    main()
