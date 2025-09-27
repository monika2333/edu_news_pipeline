#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re

def chinese_number(num):
    """将阿拉伯数字转换为中文数字"""
    chinese_nums = ['', '一', '二', '三', '四', '五', '六', '七', '八', '九', '十',
                   '十一', '十二', '十三', '十四', '十五', '十六', '十七', '十八', '十九', '二十',
                   '二十一', '二十二', '二十三', '二十四', '二十五', '二十六', '二十七', '二十八', '二十九', '三十']
    if num < len(chinese_nums):
        return chinese_nums[num]
    else:
        return str(num)  # 超出范围时返回阿拉伯数字

def add_chinese_numbers(filename):
    """给新闻条目添加中文数字编号"""

    # 读取文件内容
    with open(filename, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    # 处理文件内容
    new_lines = []
    news_counter = 0

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # 跳过前面的标题部分（首都教育舆情、总第xxx期、日期等）
        if i < 5:
            new_lines.append(lines[i])
            i += 1
            continue

        # 处理【舆情速览】和【舆情参考】部分
        if line.startswith('【'):
            new_lines.append(lines[i])
            news_counter = 0  # 重置计数器
            i += 1
            continue

        # 如果是空行，直接添加
        if not line:
            new_lines.append(lines[i])
            i += 1
            continue

        # 检查是否是新闻标题行
        # 新闻标题行的特征：非空行，且下一行是内容行（通常较长且以内容开始）
        is_news_title = False

        if i + 1 < len(lines):
            next_line = lines[i + 1].strip()
            # 如果下一行不为空且比较长（可能是新闻内容），那么当前行很可能是标题
            if next_line and len(next_line) > 50 and not next_line.startswith('【'):
                # 确保当前行不是以日期开头的内容行
                if not re.match(r'^\d+月\d+日|近日|昨日|今日', line):
                    is_news_title = True

        if is_news_title:
            news_counter += 1
            # 检查标题是否已经有编号
            if not re.match(r'^[一二三四五六七八九十]+、', line):
                numbered_line = f"{chinese_number(news_counter)}、{line}\n"
                new_lines.append(numbered_line)
            else:
                new_lines.append(lines[i])
        else:
            new_lines.append(lines[i])

        i += 1

    # 写入文件
    with open(filename, 'w', encoding='utf-8') as f:
        f.writelines(new_lines)

    print(f"[edit] {filename} (updated)")
    print(f"Done. Added Chinese numbers to {news_counter} news items.")

if __name__ == "__main__":
    filename = "0925ZM.txt"
    add_chinese_numbers(filename)