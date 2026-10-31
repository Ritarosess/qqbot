# -*- coding: utf-8 -*-
"""把动态生活状态插件的注入块改成「只当背景、禁止主动提」。

插件是第三方代码（/opt/astrbot/plugins/astrbot_plugin_dynamic_life_state/core/injector.py），
原模板只写了「话题无关时无需主动提及」，模型还是会顺口汇报自己在干嘛。
这里把开头两行替换成硬性禁令；插件升级后需要重新执行本脚本。
"""
import shutil
import sys

PATH = "/opt/astrbot/plugins/astrbot_plugin_dynamic_life_state/core/injector.py"

OLD = '''        f"<life_state>\\n"
        f"这是你当前的生活状态，可供参考\\n"
        f"话题无关时无需主动提及\\n"'''

NEW = '''        f"<life_state>\\n"
        f"这是你当前的生活状态，只作为背景参考，不是聊天内容\\n"
        f"硬性要求：除非用户明确问「你在干嘛/你在哪/在做什么」，否则一个字都不要提\\n"
        f"禁止句式：「我这儿还在……呢」「我还在……」「我刚下课」「我这边……」\\n"
        f"不要描述自己正在做什么、在哪儿、穿什么；用状态解释自己不回消息也是禁止的\\n"'''


def main() -> int:
    with open(PATH, encoding="utf-8") as f:
        text = f.read()
    if "<life_state>" not in text:
        print("没找到注入块")
        return 1
    if "硬性要求" in text:
        print("已经改过了")
        return 0
    if OLD not in text:
        print("模板和预期不一致，未修改")
        return 2
    shutil.copy2(PATH, PATH + ".bak")
    with open(PATH, "w", encoding="utf-8") as f:
        f.write(text.replace(OLD, NEW, 1))
    print("injector patched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
