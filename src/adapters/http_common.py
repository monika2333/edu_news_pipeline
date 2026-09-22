"""各 http 来源 adapter 共享的抓取与正文转换 helper。

只收敛各来源逐字等价或语义完全一致的工具函数；来源专属的抓取参数
（User-Agent、Referer、超时、重试等）按仓库约定仍留在各 adapter 内，
不要为了集中而搬进来。新增来源时优先从这里取用，不要再复制一份私有实现。
"""

from __future__ import annotations

import re
from typing import Mapping

import requests
from bs4 import BeautifulSoup

__all__ = [
    "build_session",
    "decode_response",
    "html_to_markdown",
    "strip_site_suffix",
]


def build_session(headers: Mapping[str, str], *, trust_env: bool = True) -> requests.Session:
    """用来源提供的请求头构造 Session。

    trust_env=False 时忽略系统代理与环境变量；哪些来源需要关代理由各
    adapter 自己决定并显式传参，保持抽取前后行为一致。
    """
    session = requests.Session()
    session.trust_env = trust_env
    session.headers.update(dict(headers))
    return session


def decode_response(resp: requests.Response) -> str:
    """响应解码：服务端声明编码可直接使用；缺失或误标为 iso-8859-1 时
    用 apparent_encoding 猜测（GBK 站点常见）。"""
    try:
        enc = (resp.encoding or "").lower()
    except Exception:
        enc = ""
    if not enc or enc == "iso-8859-1":
        try:
            apparent = resp.apparent_encoding or "utf-8"
            resp.encoding = apparent
        except Exception:
            resp.encoding = "utf-8"
    return resp.text or ""


def html_to_markdown(html_str: str, *, extra_unwanted: str = "") -> str:
    """正文 HTML 转纯文本段落。

    流水线只处理文字，图片整体剥离（不转 markdown 图片语法）。
    extra_unwanted 追加来源专属的噪声选择器（如北京号的
    ".share, .recommend"，北京时间的 iframe），与基础集合一并移除。
    """
    unwanted = "script, style, noscript"
    if extra_unwanted:
        unwanted = f"{unwanted}, {extra_unwanted}"
    soup = BeautifulSoup(html_str or "", "html.parser")
    for node in soup.select(unwanted):
        node.decompose()
    for image in soup.find_all("img"):
        image.decompose()
    for line_break in soup.find_all("br"):
        line_break.replace_with("\n")
    for block in soup.find_all(["p", "div", "figure", "h1", "h2", "h3", "li", "blockquote"]):
        block.insert_before("\n\n")
        block.insert_after("\n\n")
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in soup.get_text().splitlines()]
    return "\n\n".join(line for line in lines if line)


def strip_site_suffix(title: str, *suffixes: str) -> str:
    """剥离标题中「分隔符（-/_/|）+ 站点名」及其后的剩余尾巴。

    例如 `标题 - 中国新闻网 - 新闻中心` 传入 "中国新闻网" 后返回 `标题`。
    多个后缀按文档顺序取最先命中者；后缀匹配不做结尾锚定，站点名之后
    的频道尾巴一并去掉。
    """
    result = (title or "").strip()
    if not suffixes:
        return result
    pattern = r"[-|_]\s*(?:" + "|".join(re.escape(s) for s in suffixes) + r").*$"
    return re.sub(pattern, "", result).strip()
