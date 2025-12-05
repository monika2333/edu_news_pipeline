# 小红书重点舆情总结仓库

该仓库用于整理、提炼每日收集到的小红书贴文内容，提供链接提取、要点清洗等辅助脚本，方便后续在 ClaudeCode + minimax 概括流程中快速产出合规的舆情摘要。

## 核心内容
- `input_task.txt`：原始抓取的贴文内容或任务描述。
- `20251107-xiaohongshu-links.txt`（及类似文件）：按日期生成的小红书链接清单，可直接用于人工或自动化分析。
- `scripts/clean_summary_minimax.py`：在 minimax 环境里使用的文本清洗脚本，负责替换中文标点、删除收藏数、控制段落间距等。
- `extract_links.py`：读取原始文档中的 `http://xhslink.com/o/...` 链接，去重后生成 `YYYYMMDD-xiaohongshu-links.txt`。
- `xiaohongshu-post-analysis-guide.md`：小红书贴文分析指南，记录了总结口径与格式规范。

## 快速使用
1. **准备输入**：将需要解析的内容放到 `input_task.txt`，或指定其它文件路径。
2. **提取链接**  
   ```bash
   python -X utf8 extract_links.py              # 默认读取 input_task.txt
   python -X utf8 extract_links.py other.txt    # 指定其它文件
   ```
   执行后会把唯一链接写入 `YYYYMMDD-xiaohongshu-links.txt`。
3. **清洗总结**（仅在 ClaudeCode + minimax 概括场景需要）  
   ```bash
   python -X utf8 scripts\clean_summary_minimax.py
   ```
   根据提示输入需要清洗的总结文件路径，可加 `--dry-run` 先预览。

## 注意事项
- 所有脚本默认使用 UTF-8 编码，如遇到解码失败会自动尝试常见的中文编码。
- 链接提取脚本仅匹配 `http://xhslink.com/o/` 形式，如需扩展其它域名可在 `extract_links.py` 中调整正则。
- 若在 Windows 下运行，建议用 `python -X utf8 ...` 强制 UTF-8，避免本地默认编码导致乱码。
- minimax 清洗脚本仅对特定格式有效，使用前请确认输入符合“小红书帖文分析指南”里的结构。

欢迎在此基础上继续补充新的自动化脚本或分析模板。***
