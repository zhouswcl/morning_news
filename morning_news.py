import os
import re
import json
import requests
from datetime import datetime

# 从环境变量读取飞书 Webhook 地址
WEBHOOK_URL = os.environ.get("FEISHU_WEBHOOK")

# 统一的请求头（模拟正常浏览器）
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# 全局去重集合
seen_titles = set()

def safe_request(url, timeout=15):
    """安全发起 HTTP 请求，失败返回 None"""
    try:
        res = requests.get(url, headers=HEADERS, timeout=timeout)
        res.raise_for_status()
        res.encoding = res.apparent_encoding or 'utf-8'
        return res.text
    except Exception as e:
        print(f"[请求失败] {url} - 错误: {e}")
        return None

def deduplicate_and_filter(news_list, limit=5):
    """去重并限制条数"""
    unique_news = []
    for item in news_list:
        clean_title = re.sub(r'\s+', '', item.get('title', ''))
        if not clean_title or len(clean_title) < 8:
            continue
        if clean_title not in seen_titles:
            seen_titles.add(clean_title)
            unique_news.append(item)
            if len(unique_news) >= limit:
                break
    return unique_news

# ==================== 各媒体抓取逻辑（采用抗封禁策略） ====================

def fetch_yicai():
    """第一财经 (采用移动端 WAP 页面，反爬极弱)"""
    base_url = "https://m.yicai.com"
    url = f"{base_url}/news/"
    html = safe_request(url)
    if not html: return []
    try:
        soup = BeautifulSoup(html, 'lxml')
        items = []
        # WAP版结构简单，直接提取列表
        for li in soup.select("div.newsList ul li"):
            a = li.find("a", href=True)
            if a:
                title = a.get_text(strip=True)
                href = a["href"]
                if title and href:
                    items.append({"title": title, "url": href if href.startswith("http") else base_url + href})
        return deduplicate_and_filter(items)
    except Exception as e:
        print(f"[解析失败] 第一财经: {e}")
        return []

def fetch_caixin():
    """财新网 (采用内部免鉴权 JSON API，极其稳定)"""
    url = "https://gateway.caixin.com/api/newauth/appnews/list?page=1&channel=news"
    html = safe_request(url)
    if not html: return []
    try:
        data = json.loads(html)
        items = []
        if data.get("data") and data["data"].get("list"):
            for article in data["data"]["list"]:
                title = article.get("title", "").strip()
                share_url = article.get("share_url", "")
                if title and share_url:
                    items.append({"title": title, "url": share_url})
        return deduplicate_and_filter(items)
    except Exception as e:
        print(f"[解析失败] 财新网: {e}")
        return []

def fetch_eastmoney():
    """东方财富网 (采用内部 Web API，返回纯 JSON)"""
    url = "https://np-listapi.eastmoney.com/comm/web/getListInfo?cb=&client=web&type=1&mession=asf&fc=90&pageIndex=1&pageSize=10"
    html = safe_request(url)
    if not html: return []
    try:
        data = json.loads(html)
        items = []
        if data.get("data") and data["data"].get("list"):
            for article in data["data"]["list"]:
                title = article.get("title", "").strip()
                art_url = article.get("url", "")
                if title and art_url:
                    items.append({"title": title, "url": art_url})
        return deduplicate_and_filter(items)
    except Exception as e:
        print(f"[解析失败] 东方财富网: {e}")
        return []

def fetch_wallstreetcn():
    """华尔街见闻 (采用内部免鉴权 API，彻底解决 JS 渲染问题)"""
    url = "https://api.wallstreetcn.com/apiv1/content/articles?channel=global&limit=10"
    html = safe_request(url)
    if not html: return []
    try:
        data = json.loads(html)
        items = []
        if data.get("data") and data["data"].get("items"):
            for article in data["data"]["items"]:
                title = article.get("title", "").strip()
                uri = article.get("uri", "")
                if title and uri:
                    # 接口返回的 uri 缺少域名前缀，手动拼接
                    items.append({"title": title, "url": f"https://wallstreetcn.com{uri}"})
        return deduplicate_and_filter(items)
    except Exception as e:
        print(f"[解析失败] 华尔街见闻: {e}")
        return []

def fetch_21jingji():
    """21世纪经济报道 (采用移动端 WAP 页面)"""
    base_url = "https://m.21jingji.com"
    url = f"{base_url}/"
    html = safe_request(url)
    if not html: return []
    try:
        soup = BeautifulSoup(html, 'lxml')
        items = []
        for a in soup.select("div.newsList a, ul.list li a"):
            title = a.get_text(strip=True)
            href = a.get("href", "")
            if title and len(title) > 10 and href:
                items.append({"title": title, "url": href if href.startswith("http") else base_url + href})
        return deduplicate_and_filter(items)
    except Exception as e:
        print(f"[解析失败] 21世纪经济报道: {e}")
        return []

def fetch_ce():
    """中国经济网 (传统政府媒体网站，PC端直接抓取，无反爬)"""
    base_url = "http://www.ce.cn"
    url = f"{base_url}/xwzx/gnsz/gdxw/"
    html = safe_request(url)
    if not html: return []
    try:
        soup = BeautifulSoup(html, 'lxml')
        items = []
        for a in soup.select("ul.list_009 li a"):
            title = a.get_text(strip=True)
            href = a.get("href", "")
            if title and href:
                items.append({"title": title, "url": format_url(href, base_url)})
        return deduplicate_and_filter(items)
    except Exception as e:
        print(f"[解析失败] 中国经济网: {e}")
        return []

def format_url(url, base_url):
    """将相对路径转换为绝对路径"""
    if url.startswith("http"):
        return url
    if url.startswith("//"):
        return "https:" + url
    return base_url + url

# ==================== 消息格式化与推送 ====================

def format_feishu_text(all_news):
    """将抓取结果格式化为飞书 Text 消息格式"""
    now = datetime.now()
    weekday = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"][now.weekday()]
    date_str = now.strftime("%Y年%m月%d日")

    text = f"📰 财经早报 | {date_str} {weekday}\n\n"
    
    has_news = False
    for source, news_list in all_news.items():
        if not news_list:
            continue
        has_news = True
        text += f"【{source}】\n"
        for i, item in enumerate(news_list, 1):
            # 飞书 text 类型会将独立一行的 URL 自动转为可点击链接
            text += f"{i}. {item['title']}\n   {item['url']}\n"
        text += "\n"

    if not has_news:
        text += "⚠️ 今日早报数据抓取异常，所有媒体源均失败，请检查网络或爬虫策略。"

    return text

def send_to_feishu(content):
    """通过 Webhook 发送文本消息到飞书"""
    if not WEBHOOK_URL:
        print("错误: 缺少环境变量 FEISHU_WEBHOOK，无法推送。")
        return False

    payload = {
        "msg_type": "text",
        "content": {
            "text": content
        }
    }

    try:
        response = requests.post(WEBHOOK_URL, json=payload, timeout=15)
        response.raise_for_status()
        res_json = response.json()
        if res_json.get("code") == 0 or res_json.get("StatusCode") == 0:
            print("✅ 飞书消息推送成功")
            return True
        else:
            print(f"❌ 飞书推送返回错误: {res_json}")
            return False
    except Exception as e:
        print(f"❌ 飞书网络请求失败: {e}")
        return False

# ==================== 主程序 ====================

def main():
    print(f"=== 开始抓取财经早报 ===")
    
    sources = {
        "第一财经": fetch_yicai,
        "财新网": fetch_caixin,
        "东方财富网": fetch_eastmoney,
        "华尔街见闻": fetch_wallstreetcn,
        "21世纪经济报道": fetch_21jingji,
        "中国经济网": fetch_ce,
    }

    all_news = {}
    for name, fetch_func in sources.items():
        print(f"- 正在抓取: {name}...")
        try:
            all_news[name] = fetch_func()
            print(f"  成功获取 {len(all_news[name])} 条")
        except Exception as e:
            print(f"  抓取崩溃: {e}")
            all_news[name] = []

    message_text = format_feishu_text(all_news)
    print("\n=== 早报内容预览 ===")
    print(message_text)
    
    send_to_feishu(message_text)

if __name__ == "__main__":
    main()
