#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""更新搬史配置：取消强制标签，只搬抽象标签"""
import json

P = "/opt/astrbot/data/config/astrbot_plugin_aemeath_chishi_config.json"
d = json.load(open(P, encoding="utf-8-sig"))
d["feedForceTags"] = ""
d["feedRequireTags"] = "抽象"
d["feedMinLevel"] = 4
json.dump(d, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=4)
print("feedForceTags='' | feedRequireTags=抽象 | feedMinLevel=4")
