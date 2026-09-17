"""应用识别与归类。

链路：HWND -> PID -> psutil.Process -> exe 名 / 显示名，再看窗口标题细分。

判定优先级（顺序很重要）：
    1. 用户自定义覆盖
    2. 浏览器专有：必须靠标题细分，否则 Chrome 占了一半时长却只能记成"浏览器-未分类"
    3. 进程名规则（已知应用，可信度高于标题）
    4. 标题关键词（兜底，覆盖 Electron 之类进程名认不出的应用）
    5. 其他
会议检测单独走一条线，只用于"要不要静音"，不参与分类，避免标题里出现"会议"两字
就把 Word 文档错误归类。
"""
from __future__ import annotations

import os

import psutil

C_WORK = "工作"
C_STUDY = "学习"
C_SOCIAL = "社交"
C_MEETING = "会议"
C_VIDEO = "视频"
C_GAME = "游戏"
C_BROWSER = "浏览器-未分类"
C_TOOL = "工具"
C_SYSTEM = "系统"
C_OTHER = "其他"

CATEGORIES = [
    C_WORK, C_STUDY, C_SOCIAL, C_MEETING, C_VIDEO,
    C_GAME, C_BROWSER, C_TOOL, C_SYSTEM, C_OTHER,
]

# 分类在图表里对应的颜色
CATEGORY_COLORS = {
    C_WORK: "#4C8DFF",
    C_STUDY: "#38C2A6",
    C_SOCIAL: "#FFB020",
    C_MEETING: "#9B7BFF",
    C_VIDEO: "#FF7A59",
    C_GAME: "#F45B9C",
    C_BROWSER: "#7FB2FF",
    C_TOOL: "#8C9AAE",
    C_SYSTEM: "#B9C2D0",
    C_OTHER: "#CBD4E1",
}

LEISURE_CATEGORIES = {C_VIDEO, C_GAME}


# ---------------------------------------------------------------- 规则
BROWSER_EXES = {
    "chrome", "msedge", "firefox", "brave", "opera", "vivaldi",
    "360se", "360chrome", "qqbrowser", "sogouexplorer", "maxthon",
    "iexplore", "chromium", "arc", "librewolf", "waterfox",
}

# 会议专用客户端
MEETING_EXES = {
    "wemeetapp", "zoom", "webexmta", "webex", "gotomeeting", "bluejeans",
    "voov", "tencentmeeting",
}

# 标题里的会议信号（仅用于静音判定）
MEETING_TITLE_KEYWORDS = (
    "腾讯会议", "zoom meeting", "zoom 会议", "google meet", "teams 会议",
    "webex", "会议中", "正在通话", "语音通话", "视频通话", "meeting",
    "正在共享", "屏幕共享", "shared screen",
)

EXE_RULES: dict[str, str] = {}


def _fill(exes: str, category: str) -> None:
    for e in exes.split():
        EXE_RULES[e] = category


_fill(
    "code code-insiders cursor devenv pycharm64 pycharm idea64 idea goland clion "
    "webstorm rider sublime_text notepad++ vim nvim emacs eclipse matlab rstudio "
    "xmind mindmaster notion obsidian typora joplin logseq "
    "winword wps et excel powerpnt onenote acrobat acrord32 foxitreader "
    "autocad acad solidworks creo ug nx catia inventor sketchup blender "
    "photoshop illustrator premiere afterfx audition lightroom "
    "navicat dbeaver sqlyog postman apifox fiddler wireshark putty "
    "xshell xshell6 mobaxterm finalshell gitkraken sourcetree tortoisegit "
    "mstsc vmware vmplayer virtualbox imageglass snipaste pixpin quicklook "
    "typora drawio axure figma adobexd",
    C_WORK,
)

_fill(
    "anki eudic youdao_dict powerword geogebra maple comsol ansys labview "
    "multisim keil devcpp mingw32 scratch notepad calc",
    C_STUDY,
)

_fill(
    "wechat weixin qq tim dingtalk feishu lark telegram discord whatsapp "
    "line skype wecom douyin kuaishou",
    C_SOCIAL,
)

_fill(
    "potplayer mpc-hc64 mpc-hc mpv mpvnet vlc iqiyi qqlive youku bilibili "
    "kugou qqmusic cloudmusic spotify netease neteasecloudmusic foobar2000 "
    "vodplayer bdcam",
    C_VIDEO,
)

_fill(
    "steam steamwebhelper epicgameslauncher origin uplay ubisoftconnect "
    "battle.net riotclientservices wegame yuanshen genshinimpact "
    "mihoyolauncher hoyoplay",
    C_GAME,
)

_fill("wemeetapp zoom webexmta webex gotomeeting voov tencentmeeting bluejeans", C_MEETING)

_fill(
    "taskmgr explorer cmd powershell pwsh wt conhost regedit mmc "
    "systemsettings calculator mspaint snippingtool searchapp "
    "processhacker hwinfo cpuz gpuz crystaldiskinfo everything ",
    C_TOOL,
)

_fill(
    "svchost system idle csrss winlogon dwm wininit services lsass "
    "runtimebroker shellexperiencehost startmenuexperiencehost textinputhost "
    "securityhealthsystray msmpeng nissrv mpdefendercoreservice "
    "antimalware service executable searchindexer taskhostw fontdrvhost "
    "sihost ctfmon audiodg spoolsv wudfhost dllhost conhosts",
    C_SYSTEM,
)

# 标题关键词（只在浏览器与未知进程上生效）
TITLE_RULES: list[tuple[tuple[str, ...], str]] = [
    (("bilibili", "哔哩哔哩", "youtube", "netflix", "腾讯视频", "爱奇艺", "优酷",
      "芒果tv", "西瓜视频", "斗鱼", "虎牙", "twitch", "网易云音乐", "qq音乐"),
     C_VIDEO),
    (("腾讯会议", "zoom meeting", "google meet", "webex", "voov", "会议中",
      "正在通话", "视频通话"), C_MEETING),
    (("github", "gitlab", "stack overflow", "jupyter", "colab", "leetcode",
      "牛客", "慕课", "coursera", "中国大学mooc", "学习通", "知网", "csdn",
      "菜鸟教程", "w3school", "developer.mozilla", "docs.python", "readthedocs",
      "arxiv", "维基百科", "wikipedia"), C_STUDY),
    (("微信", "wechat", "qq空间", "telegram", "discord", "微博", "知乎",
      "小红书", "贴吧", "豆瓣", "抖音"), C_SOCIAL),
    (("steam", "epic games", "原神", "genshin", "league of legends", "英雄联盟",
      "minecraft", "我的世界", "csgo", "counter-strike", "dota"), C_GAME),
    (("gmail", "outlook", "阿里云", "腾讯云", "语雀", "飞书文档", "confluence",
      "jira", "notion", "石墨文档", "腾讯文档"), C_WORK),
]

DISPLAY_NAMES = {
    "code": "VS Code", "code-insiders": "VS Code Insiders", "cursor": "Cursor",
    "pycharm64": "PyCharm", "pycharm": "PyCharm", "idea64": "IntelliJ IDEA",
    "goland": "GoLand", "clion": "CLion", "webstorm": "WebStorm",
    "devenv": "Visual Studio", "sublime_text": "Sublime Text",
    "notepad++": "Notepad++", "notepad": "记事本", "winword": "Word",
    "excel": "Excel", "powerpnt": "PowerPoint", "wps": "WPS", "et": "WPS 表格",
    "onenote": "OneNote", "acrobat": "Acrobat", "acrord32": "Acrobat Reader",
    "chrome": "Chrome", "msedge": "Edge", "firefox": "Firefox",
    "brave": "Brave", "360se": "360 浏览器", "qqbrowser": "QQ 浏览器",
    "wechat": "微信", "weixin": "微信", "qq": "QQ", "tim": "TIM",
    "dingtalk": "钉钉", "feishu": "飞书", "lark": "飞书", "wecom": "企业微信",
    "wemeetapp": "腾讯会议", "zoom": "Zoom", "teams": "Teams",
    "ms-teams": "Teams", "webex": "Webex",
    "potplayer": "PotPlayer", "mpc-hc64": "MPC-HC", "vlc": "VLC", "mpv": "mpv",
    "cloudmusic": "网易云音乐", "qqmusic": "QQ 音乐", "spotify": "Spotify",
    "steam": "Steam", "explorer": "文件资源管理器", "taskmgr": "任务管理器",
    "mspaint": "画图", "powershell": "PowerShell", "wt": "Windows Terminal",
    "cmd": "命令提示符", "obsidian": "Obsidian", "notion": "Notion",
    "gitkraken": "GitKraken", "navicat": "Navicat", "dbeaver": "DBeaver",
    "postman": "Postman", "xmind": "XMind", "typora": "Typora",
    "matlab": "MATLAB", "rstudio": "RStudio", "blender": "Blender",
    "photoshop": "Photoshop", "mstsc": "远程桌面", "regedit": "注册表编辑器",
    "calc": "计算器", "snipaste": "Snipaste", "everything": "Everything",
    "systemsettings": "系统设置", "joplin": "Joplin",
}


# ---------------------------------------------------------------- 进程查询
def exe_of(pid: int) -> str:
    """进程可执行文件名（小写、去掉 .exe）。失败返回空串。"""
    if not pid:
        return ""
    try:
        name = psutil.Process(pid).name() or ""
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return ""
    if not name:
        return ""
    base = os.path.basename(name)
    if base.lower().endswith(".exe"):
        base = base[:-4]
    return base.lower()


def display_name(exe: str) -> str:
    if not exe:
        return "未知"
    if exe in DISPLAY_NAMES:
        return DISPLAY_NAMES[exe]
    return exe[:1].upper() + exe[1:]


# ---------------------------------------------------------------- 分类
def classify(
    exe: str, title: str, overrides: dict[str, str] | None = None
) -> tuple[str, str]:
    """返回 (分类, 显示名)。"""
    name = display_name(exe)
    if not exe:
        return C_OTHER, name

    if overrides and exe in overrides:
        return overrides[exe], name

    low = (title or "").lower()

    # 浏览器 / 未知进程：靠标题判定
    if exe in BROWSER_EXES:
        for keywords, category in TITLE_RULES:
            if any(k in low for k in keywords):
                return category, name
        return C_BROWSER, name

    if exe in EXE_RULES:
        return EXE_RULES[exe], name

    for keywords, category in TITLE_RULES:
        if any(k in low for k in keywords):
            return category, name

    return C_OTHER, name


def title_suggests_meeting(title: str) -> bool:
    low = (title or "").lower()
    return any(k in low for k in MEETING_TITLE_KEYWORDS)


def is_meeting(exe: str, title: str) -> bool:
    """是否正在进行会议（决定要不要自动静音）。"""
    if exe in MEETING_EXES:
        return True
    return title_suggests_meeting(title)
