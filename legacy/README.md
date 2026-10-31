# legacy/ —— 早期自研 bot.py（仅回滚用）

这里是从仓库根目录整理进来的**早期自研版本**（systemd + `bot.py`），线上已经换成 AstrBot 架构，本目录平时不用管，只有在需要回滚旧版时才用。

| 文件 | 说明 |
|---|---|
| `bot.py` | 旧版入口（systemd `qqbot.service` 的 ExecStart 指向它） |
| `ai_client.py` | 旧的 LLM 调用封装 |
| `conversation.py` / `message_handler.py` | 旧的会话与消息处理 |
| `voice` 相关：`minimax_voice.py` / `create_voice.py` / `prep_voice_sample.py` | 旧版语音合成与样本处理 |
| `emoji_manager.py` / `wuwa_emoji.py` | 旧版表情管理 |
| `whitelist.py` | 旧版群白名单 |
| `config.example.py` | 配置样例（`config.py` 含敏感信息，已被 .gitignore 忽略） |
| `requirements.txt` | 旧版依赖 |

部署方式见 [../deploy/README.md](../deploy/README.md)；注意部署脚本会把这些文件同步到服务器 `/opt/qqbot/` 根目录，保持旧路径不变。
