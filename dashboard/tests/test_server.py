# -*- coding: utf-8 -*-
"""设置面板后端的小测试：不依赖服务器，用临时目录模拟插件与配置。"""
import json
import os
import shutil
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# 必须在 import server 之前设置环境变量，server 用它们定位目录
TMP = ROOT / "_tmp_dash_test"
shutil.rmtree(TMP, ignore_errors=True)
(TMP / "config").mkdir(parents=True)
(TMP / "plugins").mkdir(parents=True)
os.environ["DASH_CONFIG_DIR"] = str(TMP / "config")
os.environ["DASH_PLUGIN_DIR"] = str(TMP / "plugins")
os.environ["DASH_TOKEN_FILE"] = str(TMP / "token")

sys.path.insert(0, str(ROOT))
import server  # noqa: E402


def make_plugin(name, schema, config=None):
    pdir = TMP / "plugins" / name
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "_conf_schema.json").write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
    (pdir / "metadata.yaml").write_text(f"name: {name}\ndisplay_name: 测试插件\ndesc: 说明\n",
                                        encoding="utf-8")
    if config is not None:
        (TMP / "config" / f"{name}_config.json").write_text(
            json.dumps(config, ensure_ascii=False), encoding="utf-8")


class DashboardTest(unittest.TestCase):
    def test_list_plugins_and_save(self):
        make_plugin("astrbot_plugin_demo", {
            "enabled": {"type": "bool", "default": True},
            "count": {"type": "int", "default": 1},
            "prob": {"type": "float", "default": 0.3},
            "name": {"type": "string", "default": "x"},
            "text": {"type": "text", "default": ""},
        }, {"enabled": True, "count": 1, "prob": 0.3, "name": "x", "text": ""})

        plugins = server.list_plugins()
        self.assertEqual([p["name"] for p in plugins], ["astrbot_plugin_demo"])
        self.assertEqual(plugins[0]["display_name"], "测试插件")
        self.assertIn("enabled", plugins[0]["schema"])

        cfg = server.save_plugin("astrbot_plugin_demo", {
            "enabled": "false", "count": "7", "prob": "0.25", "name": "标题",
            "text": "多行\n文本", "unknown_key": "应被忽略",
        })
        self.assertFalse(cfg["enabled"])
        self.assertEqual(cfg["count"], 7)
        self.assertAlmostEqual(cfg["prob"], 0.25)
        self.assertEqual(cfg["name"], "标题")
        self.assertEqual(cfg["text"], "多行\n文本")
        self.assertNotIn("unknown_key", cfg)

        on_disk = json.loads((TMP / "config" / "astrbot_plugin_demo_config.json").read_text(encoding="utf-8"))
        self.assertEqual(on_disk["count"], 7)
        self.assertTrue((TMP / "config" / "astrbot_plugin_demo_config.json.bak").exists())

    def test_unknown_plugin(self):
        with self.assertRaises(KeyError):
            server.save_plugin("not_exists", {"x": 1})

    def test_read_bom(self):
        path = TMP / "bom.json"
        path.write_text('\ufeff{"a": 1}', encoding="utf-8")
        self.assertEqual(server.read_json(path), {"a": 1})


    def test_memory_actions(self):
        mem_dir = TMP / "memory"
        mem_dir.mkdir(parents=True, exist_ok=True)
        old = os.environ.get("DASH_MEMORY_DIR")
        os.environ["DASH_MEMORY_DIR"] = str(mem_dir)
        server.MEMORY_DIR = str(mem_dir)
        server.MEMORY_FILE = str(mem_dir / "memory.json")
        server.MEMORY_REQ = str(mem_dir / "backfill_request.json")
        server.MEMORY_STATUS = str(mem_dir / "backfill_status.json")
        try:
            (mem_dir / "memory.json").write_text(json.dumps({"records": [
                {"id": "aa", "text": "阿伟在准备考研", "scope": "group:1", "clarity": "clear",
                 "importance": 70, "last_seen": 0},
                {"id": "bb", "text": "主人喜欢冰美式", "scope": "global", "clarity": "vivid",
                 "importance": 80, "pinned": True, "last_seen": 0},
            ]}, ensure_ascii=False), encoding="utf-8")

            state = server.memory_state()
            self.assertEqual(state["total"], 2)
            self.assertEqual(state["by_clarity"]["vivid"], 1)
            self.assertEqual(state["by_scope"]["全局"], 1)

            self.assertEqual(server.memory_state("考研")["matched"], 1)

            server.memory_action({"action": "add", "text": "群友小刘养了只猫叫团子"})
            self.assertEqual(server.memory_state()["total"], 3)

            server.memory_action({"action": "delete", "id": "aa"})
            self.assertEqual(server.memory_state()["total"], 2)

            server.memory_action({"action": "backfill", "days": 5})
            req = json.loads((mem_dir / "backfill_request.json").read_text(encoding="utf-8"))
            self.assertEqual(req["days"], 5)

            server.memory_action({"action": "clear"})
            self.assertEqual(server.memory_state()["total"], 0)

            with self.assertRaises(ValueError):
                server.memory_action({"action": "nope"})
        finally:
            if old is None:
                os.environ.pop("DASH_MEMORY_DIR", None)
            else:
                os.environ["DASH_MEMORY_DIR"] = old

    def test_known_groups(self):
        # 配置里的逗号名单 + 日记 umo.json 都会被收集
        (TMP / "config" / "astrbot_plugin_demo_config.json").write_text(
            json.dumps({"feedGroups": "123456,234567", "feedBlockList": ""}, ensure_ascii=False),
            encoding="utf-8")
        umo_dir = TMP / "data" / "plugin_data" / "astrbot_plugin_aemeath_diary"
        umo_dir.mkdir(parents=True)
        (umo_dir / "umo.json").write_text(json.dumps({"345678": "x"}), encoding="utf-8")
        old = os.environ.get("DASH_DATA_DIR")
        os.environ["DASH_DATA_DIR"] = str(TMP / "data")
        try:
            groups = server.known_groups()
        finally:
            if old is None:
                os.environ.pop("DASH_DATA_DIR", None)
            else:
                os.environ["DASH_DATA_DIR"] = old
        self.assertIn("123456", groups)
        self.assertIn("234567", groups)
        self.assertIn("345678", groups)
        self.assertNotIn("1", groups)  # 太短的数字不算群号

    def test_activity_levels(self):
        # 活跃度：写入群聊插话插件配置，并能反推出当前档位
        make_plugin(server.ACTIVITY_PLUGIN, {
            "enabled": {"type": "bool", "default": True},
            "probability": {"type": "float", "default": 0.2},
            "cooldown": {"type": "int", "default": 60},
            "min_len": {"type": "int", "default": 2},
        }, {"enabled": True, "probability": 0.05, "cooldown": 300, "min_len": 4})
        self.addCleanup(shutil.rmtree, TMP / "plugins" / server.ACTIVITY_PLUGIN, True)
        self.addCleanup((TMP / "config" / f"{server.ACTIVITY_PLUGIN}_config.json").unlink, True)

        state = server.activity_state()
        self.assertEqual(state["level"], "normal")
        self.assertEqual(len(state["levels"]), 5)

        server.set_activity("quiet")
        cfg = json.loads((TMP / "config" / f"{server.ACTIVITY_PLUGIN}_config.json")
                         .read_text(encoding="utf-8"))
        self.assertEqual(cfg["probability"], 0.01)
        self.assertEqual(cfg["cooldown"], 900)
        self.assertEqual(cfg["min_len"], 6)
        self.assertEqual(server.activity_state()["level"], "quiet")

        with self.assertRaises(ValueError):
            server.set_activity("nope")


if __name__ == "__main__":
    unittest.main(verbosity=2)
