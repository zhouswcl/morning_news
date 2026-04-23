import os
import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime

# 从环境变量读取飞书 Webhook 地址
WEBHOOK_URL = os.environ.get("FEISHU_WEBHOOK")

# 统一请求头
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# 全局去重集合
seen_titles = set()

def safe_request(url, timeout=15):
    """安全发起 HTTP 请求"""
    try:
        res = requests.get(url, headers=HEADERS, timeout=timeout)
        res.raise_for_status()
        res.encoding = 'utf-8'
        return res.text
    except Exception as e:
        print(f"[请求失败] {url} - {e}")
        return None

def resolve_redirect(url, timeout=8):
    """解析必应的跳转链接，获取真实的新闻原始 URL，确保链接一定能点开"""
    try:
        # 禁止自动跳转，只获取响应头中的 Location
        res = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=False)
        if res.status_code in [301, 302, 303, 307, 308]:
            real_url = res.headers.get('Location', url)
            # 清理可能存在的换行符
            return real_url.replace('\n', '').replace('\r', '')
        return url
    except:
        return url

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

def fetch_from_bing(domain, source_name):
    """统一抓取策略：通过必应新闻精准搜索获取指定媒体的最新新闻"""
    # 构造精准搜索词：限定必须是该域名下的新闻
    url = f"https://www.bing.com/news/search?q=site:{domain}&form=PTFTNR"
    html = safe_request(url)
    if not html: return []
    
    try:
        soup = BeautifulSoup(html, 'lxml')
        items = []
        # 必应新闻页面的标题结构极其稳定，都在 a.title 标签中
        for a in soup.select("a.title"):
            title = a.get_text(strip=True)
            href = a.get("href", "")
            if title and href:
                # 剥离必应跳转外壳，拿到真实原网址
                real_url = resolve_redirect(href)
                items.append({"title": title, "url": real_url})
        return deduplicate_and_filter(items)
    except Exception as e:
        print(f"[解析失败] {source_name}: {e}")
        return []

# ==================== 消息格式化与推送 ====================

def format_feishu_text(all_news):
    """将抓取结果格式化为飞书 Text 消息"""
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
        text += "⚠️ 今日早报数据抓取异常。"

    return text

def send_to_feishu(content):
    """推送飞书消息"""
    if not WEBHOOK_URL:
        print("错误: 缺少环境变量 FEISHU_WEBHOOK")
        return False
    try:
        payload = {
            "msg_type": "text",
            "content": {"text": content}
        }
        res = requests.post(WEBHOOK_URL, json=payload, timeout=15)
        res.raise_for_status()
        print("✅ 飞书消息推送成功")
        return True
    except Exception as e:
        print(f"❌ 飞书网络请求失败: {e}")
        return False

# ==================== 主程序 ====================

def main():
    print("=== 开始抓取财经早报 ===")
    
    # 媒体名称与域名的映射关系
    sources = {
        "第一财经": "yicai.com",
        "财新网": "caixin.com",
        "东方财富网": "eastmoney.com",
        "华尔街见闻": "wallstreetcn.com",
        "21世纪经济报道": "21jingji.com",
        "中国经济网": "ce.cn",
    }

    all_news = {}
    for name, domain in sources.items():
        print(f"- 正在抓取: {name}...")
        try:
            all_news[name] = fetch_from_bing(domain, name)
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
