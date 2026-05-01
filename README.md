# TaikoNijiiroDondaEx 曲谱开头剪裁

一个用于 Taiko / Nijiiro / DondaEx 曲谱的 TJA + OGG 开头剪裁工具。  
输入目标秒数后，程序会生成新的 `.tja` 与 `.ogg`，并可给新曲名添加自定义前缀。

## 普通用户下载

不需要安装 Python，也不需要单独安装 ffmpeg。

1. 打开本项目的 GitHub Releases 页面。
2. 下载最新版本里的 `TaikoNijiiroDondaEx曲谱开头剪裁.exe`。
3. 双击运行。

> 不建议普通用户下载绿色的 `Code` 源码包；源码包主要给开发者使用。

## 使用方法

1. 选择原始 `OGG` 音源。
2. 选择对应的 `TJA` 谱面。
3. 输入要剪到的目标秒数。
4. 可选：填写自定义前缀。新文件名与游戏内标题会变为「前缀 + 原名」。
5. 点击「分析」，确认结果。
6. 点击「生成新文件」。

程序不会覆盖原始谱面和音频，会在 TJA 同目录生成新的 `.tja` 与 `.ogg`。

## 关于为什么只剪裁开头不剪裁结尾

TaikoNijiiroDondaEx 按 F1 就能直接从头开始，没有剪裁结尾的必要。

## 开发者运行

需要 Python 3.10+。

```powershell
py -3 tja_ogg_measure_trimmer.py
```

源码运行时，程序会按以下位置寻找 `ffmpeg.exe`：

- 程序目录
- `ffmpeg\`
- `ffmpeg\bin\`
- `vendor\ffmpeg\bin\`
- 系统 PATH

## 发布说明

仓库只保存源码和说明文件；面向普通用户的一键版本请通过 GitHub Releases 发布。

本地封装时需要准备：

- `logo.png`
- `vendor\ffmpeg\bin\ffmpeg.exe`
- PyInstaller
- Pillow

封装命令示例：

```powershell
py -3 -m pip install pyinstaller pillow
py -3 -m PyInstaller --noconfirm --clean --onefile --windowed `
  --name "TaikoNijiiroDondaEx曲谱开头剪裁" `
  --icon "logo.png" `
  --add-data "logo.png;." `
  --add-binary "vendor\ffmpeg\bin\ffmpeg.exe;." `
  "tja_ogg_measure_trimmer.py"
```

生成后，将 `dist\TaikoNijiiroDondaEx曲谱开头剪裁.exe` 上传到 GitHub Release 的 Assets。

## 第三方组件

封装版内置 ffmpeg。ffmpeg 是独立的第三方项目，请遵守其许可证要求：  
https://ffmpeg.org/legal.html
