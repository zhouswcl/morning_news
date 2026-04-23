import os
import re
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime

# 从环境变量读取飞书 Webhook 地址
WEBHOOK_URL = os.environ.get("FEISHU_WEBHOOK")

# 统一的请求头，模拟正常浏览器访问
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# 用于全局去重的集合
seen_titles = set()

def safe_request(url, timeout=10):
    """安全发起 HTTP 请求，失败返回 None"""
    try:
        res = requests.get(url, headers=HEADERS, timeout=timeout)
        res.raise_for_status()
        res.encoding = res.apparent_encoding or 'utf-8'
        return res.text
    except Exception as e:
        print(f"[请求失败] {url} - 错误: {e}")
        return None

def format_url(url, base_url):
    """将相对路径转换为绝对路径"""
    if url.startswith("http"):
        return url
    if url.startswith("//"):
        return "https:" + url
    return base_url + url

def deduplicate_and_filter(news_list, limit=5):
    """去重并限制条数"""
    unique_news = []
    for item in news_list:
        # 去除空格和换行后进行去重比对
        clean_title = re.sub(r'\s+', '', item.get('title', ''))
        if not clean_title or len(clean_title) < 8:
            continue
        if clean_title not in seen_titles:
            seen_titles.add(clean_title)
            unique_news.append(item)
            if len(unique_news) >= limit:
                break
    return unique_news

# ==================== 各媒体抓取逻辑 ====================

def fetch_yicai():
    """第一财经"""
    base_url = "https://www.yicai.com"
    url = f"{base_url}/news/"
    html = safe_request(url)
    if not html: return []
    try:
        soup = BeautifulSoup(html, 'lxml')
        items = []
        for a in soup.select("div.f-node-v2 a"):
            title = a.get_text(strip=True)
            href = a.get("href", "")
            if title and href:
                items.append({"title": title, "url": format_url(href, base_url)})
        return deduplicate_and_filter(items)
    except Exception as e:
        print(f"[解析失败] 第一财经: {e}")
        return []

def fetch_caixin():
    """财新网"""
    base_url = "https://www.caixin.com"
    url = base_url
    html = safe_request(url)
    if not html: return []
    try:
        soup = BeautifulSoup(html, 'lxml')
        items = []
        # 财新网首页通常混合了头条和列表
        for a in soup.select("div.topNews h3 a, div.secMain ul li a, div.cons ul li a"):
            title = a.get_text(strip=True)
            href = a.get("href", "")
            if title and href:
                items.append({"title": title, "url": format_url(href, base_url)})
        return deduplicate_and_filter(items)
    except Exception as e:
        print(f"[解析失败] 财新网: {e}")
        return []

def fetch_eastmoney():
    """东方财富网"""
    base_url = "https://finance.eastmoney.com"
    url = f"{base_url}/a/czqyw.html"
    html = safe_request(url)
    if not html: return []
    try:
        soup = BeautifulSoup(html, 'lxml')
        items = []
        for a in soup.select("div.txtinfo > a, ul.list li a"):
            title = a.get_text(strip=True)
            href = a.get("href", "")
            if title and href:
                items.append({"title": title, "url": format_url(href, base_url)})
        return deduplicate_and_filter(items)
    except Exception as e:
        print(f"[解析失败] 东方财富网: {e}")
        return []

def fetch_wallstreetcn():
    """华尔街见闻 (注: 该网站强依赖JS渲染，纯请求可能获取不到数据，已做容错降级)"""
    base_url = "https://wallstreetcn.com"
    url = f"{base_url}/news/global"
    html = safe_request(url)
    if not html: return []
    try:
        soup = BeautifulSoup(html, 'lxml')
        items = []
        # 尝试匹配可能存在的静态 DOM 结构
        for a in soup.select("a[href*='/articles/'], a[href*='/news/']"):
            title = a.get_text(strip=True)
            href = a.get("href", "")
            if title and len(title) > 10 and href:
                items.append({"title": title, "url": format_url(href, base_url)})
        return deduplicate_and_filter(items)
    except Exception as e:
        print(f"[解析失败] 华尔街见闻: {e}")
        return []

def fetch_21jingji():
    """21世纪经济报道"""
    base_url = "https://www.21jingji.com"
    url = base_url
    html = safe_request(url)
    if not html: return []
    try:
        soup = BeautifulSoup(html, 'lxml')
        items = []
        for a in soup.select("div.m-list li a, div.list-art li a"):
            title = a.get_text(strip=True)
            href = a.get("href", "")
            if title and href:
                items.append({"title": title, "url": format_url(href, base_url)})
        return deduplicate_and_filter(items)
    except Exception as e:
        print(f"[解析失败] 21世纪经济报道: {e}")
        return []

def fetch_ce():
    """中国经济网"""
    base_url = "http://www.ce.cn"
    url = f"{base_url}/xwzx/gnsz/gdxw/"
    html = safe_request(url)
    if not html: return []
    try:
        soup = BeautifulSoup(html, 'lxml')
        items = []
        for a in soup.select("ul.list_009 li a, div.cospace ul li a"):
            title = a.get_text(strip=True)
            href = a.get("href", "")
            if title and href:
                items.append({"title": title, "url": format_url(href, base_url)})
        return deduplicate_and_filter(items)
    except Exception as e:
        print(f"[解析失败] 中国经济网: {e}")
        return []

# ==================== 消息格式化与推送 ====================

def format_feishu_text(all_news):
    """将抓取结果格式化为飞书 Text 消息格式 (飞书Text类型会自动将独立链接变为可点击)"""
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
            # 为了在飞书 Text 消息类型中保证链接可点击，将 URL 独立成一行
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
    
    # 定义媒体源及对应的抓取函数
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

    # 格式化消息
    message_text = format_feishu_text(all_news)
    print("\n=== 早报内容预览 ===")
    print(message_text)
    
    # 发送飞书
    send_to_feishu(message_text)

if __name__ == "__main__":
    main()
