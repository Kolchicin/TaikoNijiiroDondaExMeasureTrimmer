# TaikoNijiiroDondaEx 曲谱开头剪裁

Taiko / Nijiiro / DondaEx 用的 TJA + OGG 开头剪裁工具。

## 下载

1. 打开 GitHub Releases。
2. 下载 `TaikoNijiiroDondaEx曲谱开头剪裁.exe`。
3. 双击运行。

无需安装 Python 或 ffmpeg。

## 功能

- 按目标秒数裁掉曲谱和音频开头。
- 生成新的 `.tja` 与 `.ogg`，不覆盖原文件。
- 可添加自定义前缀，同步修改文件名和游戏内标题。

## 使用

1. 选择原始 `OGG` 音源。
2. 选择对应的 `TJA` 谱面。
3. 输入目标秒数。
4. 可选：填写自定义前缀。
5. 点击「分析」。
6. 点击「生成新文件」。

输出文件会生成在 TJA 同目录。

## 设计说明

本工具只处理开头剪裁。TaikoNijiiroDondaEx 可通过 F1 从头重新开始，通常不需要额外剪裁结尾。

## 源码运行

需要 Python 3.10+ 和 ffmpeg。

```powershell
py -3 tja_ogg_measure_trimmer.py
```

## 说明

封装版内置 ffmpeg。许可证信息：<https://ffmpeg.org/legal.html>
