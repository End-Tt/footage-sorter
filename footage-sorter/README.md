# footage-sorter（素材分类）

Codex / Claude Code 技能：把一个文件夹里的视频素材自动分类整理。AI 看每条素材的 6 帧缩略图，按固定词表打标签（场景、光线、景别、运镜、人物、技术问题、0-3 分评分、风格、用途、重复组、一句话描述），按日期和行程分地点，输出离线目录网页、CSV 表格，以及导入 Final Cut Pro 后关键词按文件夹分好、好素材标收藏、废片标拒绝的 FCPXML。原素材只读，不移动不修改。

需要：Python 3.9+、ffmpeg/ffprobe、Pillow（`scripts/doctor.py` 会检查）。用法见 `skills/footage-sorter/SKILL.md`，安装方法见上一层的「使用说明.md」。
