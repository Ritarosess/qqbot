# Vendored: kurobbs-wiki

- 来源：https://github.com/Alphamancer/kurobbs-wiki （ClawHub: @alphamancer/kurobbs-wiki, v0.1.0）
- 许可：MIT License（见同目录 LICENSE，Copyright (c) 2026 VBBB）
- 引入日期：2026-10-23，取自 upstream main 分支
- 引入内容：`scripts/wikiquery.py`（纯 Python 标准库的库街区 WIKI 查询 CLI）
- 未引入：
  - `scripts/kuro_login.py`（账号登录，需浏览器 + 极验滑块，bot 场景不适用）
  - `scripts/post_fetch.py`（社区帖媒体抓取，需 Playwright/Chromium，服务器容器无此依赖）
  - `references/`（wikiquery.py 运行时不读取）
- 修改：无（原样引入；升级时整文件替换并同步本说明）

上层 `main.py` 通过子进程调用该脚本（tree/list/detail/search/probe/pair/team/candidates），
HOME 环境变量重定向到插件数据目录以持久化其 `~/.kurobbs-wiki-cache` 缓存。

注意：库街区 API 为无鉴权私有接口，字段可能随站点改版变化；脚本内置 0.05s 限速，
插件侧另有串行锁与会话冷却，请勿移除。
