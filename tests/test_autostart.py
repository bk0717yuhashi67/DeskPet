"""开机自启的路径校验与自愈逻辑测试。

不碰真实注册表：把 `paths.autostart_value` 临时替换成固定值，只测纯逻辑。
涉及"路径失效"的用例是这次的要害——项目从 E:\\DeskPet 搬到
E:\\Vibe Coding\\DeskPet 后，注册表里的值仍然非空，只是路径不存在了。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import paths  # noqa: E402


def _fake_value(value):
    """把 paths.autostart_value 临时替换掉，返回用于还原的函数。"""
    saved = paths.autostart_value
    paths.autostart_value = lambda: value

    def undo():
        paths.autostart_value = saved

    return undo


class TestCommandPathsExist(unittest.TestCase):
    """命令里引号括起来的路径是否都真实存在。"""

    def test_single_existing_path(self):
        self.assertTrue(paths._command_paths_exist(f'"{sys.executable}"'))

    def test_two_existing_paths(self):
        cmd = f'"{sys.executable}" "{paths.ROOT / "main.py"}"'
        self.assertTrue(paths._command_paths_exist(cmd))

    def test_missing_path(self):
        self.assertFalse(paths._command_paths_exist('"E:\\Nonexistent\\a.exe"'))

    def test_one_of_two_missing(self):
        cmd = f'"{sys.executable}" "E:\\Nonexistent\\main.py"'
        self.assertFalse(paths._command_paths_exist(cmd))

    def test_without_quotes_is_invalid(self):
        """没有引号就无法可靠切分，直接判无效（Windows 的 Run 值一向带引号）。"""
        self.assertFalse(paths._command_paths_exist("pythonw.exe main.py"))

    def test_empty_string(self):
        self.assertFalse(paths._command_paths_exist(""))


class TestAutostartIsValid(unittest.TestCase):
    """`autostart_is_valid` 不能只看值非空，必须校验路径。"""

    def test_not_configured(self):
        undo = _fake_value(None)
        try:
            self.assertFalse(paths.autostart_is_valid())
        finally:
            undo()

    def test_valid_command(self):
        undo = _fake_value(f'"{sys.executable}"')
        try:
            self.assertTrue(paths.autostart_is_valid())
        finally:
            undo()

    def test_stale_path_is_invalid(self):
        """项目搬家的情形：值还在，路径没了 —— 必须判为无效。

        只看"值非空"会返回 True，界面就会显示"已开启"却其实起不来。
        """
        undo = _fake_value('"E:\\DeskPet\\.venv\\Scripts\\pythonw.exe" "E:\\DeskPet\\main.py"')
        try:
            self.assertFalse(paths.autostart_is_valid())
        finally:
            undo()

    def test_launch_command_paths_really_exist(self):
        """`launch_command()` 产出的命令，路径必须真实存在。"""
        self.assertTrue(paths._command_paths_exist(paths.launch_command()))


class TestSyncAutostart(unittest.TestCase):
    """自检：只在"用户开了自启、但路径失效"时才改写。

    这里只覆盖不写注册表的两条分支；"路径失效→改写"的分支会真的动注册表，
    放在手工验证里做（见本轮实测记录），不进单元测试。
    """

    def test_off_when_not_configured(self):
        """没配自启就什么都不做 —— 不擅自帮用户开启。"""
        undo = _fake_value(None)
        try:
            self.assertEqual(paths.sync_autostart(), "off")
        finally:
            undo()

    def test_ok_when_already_valid(self):
        undo = _fake_value(f'"{sys.executable}"')
        try:
            self.assertEqual(paths.sync_autostart(), "ok")
        finally:
            undo()


class TestAutostartConstants(unittest.TestCase):
    def test_key_name(self):
        self.assertEqual(paths.AUTOSTART_KEY, "DeskPetPenguin")

    def test_run_key_path(self):
        self.assertEqual(
            paths.AUTOSTART_RUN_KEY,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
        )


if __name__ == "__main__":
    unittest.main()
