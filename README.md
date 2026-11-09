# 素材分类（footage-sorter）

让 AI 看缩略图，把一堆旅行 / 生活视频素材自动分类整理。

- **整理进新文件夹**：按场景分好子文件夹（雪山、海边海岸、公路行车……），文件名带星级和一句话描述，另有「★★★ 精选」「✕ 废片」。同一块 Mac（APFS）硬盘上用克隆，几乎不占空间。
- **离线目录网页**：每条素材 6 帧缩略图，能按场景、用途、评分筛选和搜索，点一下就播放原视频。
- **表格**：每条素材一行（场景、光线、景别、运镜、人物、技术问题、0–3 分评分、风格、用途、重复组、描述）。
- **Final Cut Pro（可选）**：导入后关键词按类别分进文件夹，精选标收藏，废片标拒绝。

原素材只读，不移动、不改名、不转码。所有处理都在你自己的电脑上进行，AI 分类走你自己的 Codex（ChatGPT 账号）或 Claude Code。

## 三种用法

**1. 桌面程序（最简单）**：到 [Releases](https://github.com/End-Tt/footage-sorter/releases) 下载 `footage-sorter-版本号-macOS.zip`，解压后把「素材分类.app」拖进「应用程序」。选文件夹 → 开始整理，就这么简单。第一次打开如果被系统拦住，去 **系统设置 › 隐私与安全性** 点「仍要打开」。

**2. Codex 插件**（在终端运行）：

```
codex plugin marketplace add End-Tt/footage-sorter
codex plugin add footage-sorter@sucai-tools
```

然后对 Codex 说：「用 footage-sorter 技能，把 ~/Movies/我的素材 里的素材分类」。

**3. Claude Code 插件**（在 Claude Code 里输入）：

```
/plugin marketplace add End-Tt/footage-sorter
/plugin install footage-sorter@sucai-tools
```

然后说：「帮我把 ~/Movies/我的素材 里的素材分类」。

不想用插件的话，也可以下载这个仓库，双击 `双击安装.command`，它会把技能复制进 Codex 和 Claude Code。插件和这个方式二选一就行。

## 需要什么

- macOS 13 或更新（Apple 芯片、Intel 都行）
- Codex（命令行版或 ChatGPT / Codex 桌面版都行）或 Claude Code，已登录
- Python 3、ffmpeg（`brew install ffmpeg`）、Pillow（`python3 -m pip install --user pillow`）。程序会自动检查，缺什么会提示。

详细说明见 [使用说明.md](使用说明.md)。

## 速度和额度

用 Codex、推理强度「中」实测：27 条素材从扫描到出成品约 80 秒，其中 AI 分类不到 1 分钟。AI 分类用的是你自己账号的额度；素材多的话，第一次先拿一两百条试。

## 目录结构

| 路径 | 是什么 |
|---|---|
| `footage-sorter/skills/footage-sorter/` | 技能本体：`SKILL.md`、标签词表 `vocab.json`、脚本 `scripts/` |
| `footage-sorter/.codex-plugin/`、`.claude-plugin/` | Codex / Claude Code 插件清单 |
| `.agents/plugins/`、`.claude-plugin/`（根目录） | 插件市场清单 |
| `app/` | 桌面程序源码（SwiftUI）和编译脚本 `build_app.sh` |
| `双击安装.command` | 把技能复制进 Codex / Claude Code |

自己编译桌面程序：装好 Xcode 命令行工具后运行 `app/build_app.sh`。
