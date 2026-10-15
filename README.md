# Blender 柔性电路绘图 Skill · 中文使用说明

把真实科研示意图修改过程中反复出现的问题整理为可复用经验：透明柔性包层、贴附电路、黑色同轴环、实心双绞纤芯、螺旋延长与正圆截面修复。

适合让 Codex 接手已有 Blender 场景，理解修改范围后生成独立新版本。仓库包含 skill、中文复盘和三种实用脚本；原始科研 `.blend`、CAD、照片及聊天全文不随仓库发布。

## 内容

| 文件 | 用途 |
| --- | --- |
| [SKILL.md](SKILL.md) | 模型读取的操作经验与核验流程 |
| [references/iteration-lessons.md](references/iteration-lessons.md) | 修改演变、失败原因及 V8 结论边界 |
| [scripts/inspect_scene.py](scripts/inspect_scene.py) | 读取场景，导出拓扑、圆截面、材质与依赖 JSON |
| [scripts/round_core_sections.py](scripts/round_core_sections.py) | 沿现有中心线重建固定半径正圆截面 |
| [scripts/render_preview.py](scripts/render_preview.py) | 保留当前相机、材质和灯光生成 PNG 预览 |

## 安装到 Codex

先克隆仓库，再将其复制为本地 skill。下面是 PowerShell 示例，仓库目录可以放在任意磁盘：

```powershell
git clone https://github.com/jiahehu2000-lgtm/blender-flexible-circuit-skill.git
Set-Location .\blender-flexible-circuit-skill

$skillBase = if ($env:CODEX_HOME) {
    Join-Path $env:CODEX_HOME 'skills'
} else {
    Join-Path $env:USERPROFILE '.codex\skills'
}
$skillTarget = Join-Path $skillBase 'blender-flexible-circuit-skill'
if (Test-Path -LiteralPath $skillTarget) {
    throw "目标 skill 已存在，请先检查是否需要更新：$skillTarget"
}
New-Item -ItemType Directory -Path $skillTarget -Force | Out-Null
Copy-Item -LiteralPath '.\SKILL.md','.\README.md','.\agents','.\scripts','.\references' -Destination $skillTarget -Recurse
```

在新对话中明确调用：

```text
使用 $blender-flexible-circuit-skill，读取 D:\work\final.blend。
先核验两根纤芯是否为闭合实心杆及正圆截面；保持当前颜色、器件、相机和灯光，
需要修复时另存 final_round.blend，再输出预览和检查报告。
```

也可以说明真实任务，例如“缩短黑色套环，保持靠近纤维的一端不动”，或“延长双绞结构，新增部分继续旋转，保持线径”。修改长度请说明是累计值还是额外增量，以及目标端和坐标单位。

## 直接运行脚本

三个脚本均由 **Blender 自带的 Python** 执行，不需要单独安装 `bpy` 或 pip 依赖。开发与验证环境为 Blender 5.1.1；其他版本需要自行核验。先替换下面两个示例路径：

```powershell
$blender = 'E:\app\blander\blender.exe'
$model = 'D:\work\final.blend'
```

检查场景（不修改源模型）：

```powershell
& $blender --background --python-exit-code 1 --python '.\scripts\inspect_scene.py' -- `
    --input $model --report 'D:\work\final_audit.json'
```

修复正圆截面，输出为新文件：

```powershell
& $blender --background --python-exit-code 1 --python '.\scripts\round_core_sections.py' -- `
    --input $model --output 'D:\work\final_round.blend'
```

该脚本默认识别两根名称含 `twisted_wire` 和 `solid` 的网格，也可用 `--objects core_a core_b` 指定对象。输入必须具有 `centerline_rings`、`solid_radius` 自定义属性，顶点连续按截面环排列，两芯的环数与每环顶点数匹配，侧面连接相邻环且仅有两个完整端盖。来源不明的网格不能直接套用。脚本保持中心线和现有材质，只修复截面，不自动延长纤芯。已有输出会被拒绝覆盖。

从保存的当前相机生成预览：

```powershell
& $blender --background --python-exit-code 1 --python '.\scripts\render_preview.py' -- `
    --input $model --output 'D:\work\preview.png' --width 1600 --samples 24
```

默认沿用当前背景、长宽比、渲染引擎、相机和灯光。加 `--white-background` 可将 World 设为白色并关闭透明背景，但地面、阴影和色彩管理仍可能使画面呈灰白色；它不保证每个背景像素都是纯白。预览脚本不保存源 `.blend`，也拒绝覆盖已有 PNG。

自动化请保留 `--python-exit-code 1`，脚本报错时检查进程退出码，不能只看是否出现了某条日志。具体选项可在对应命令的最后使用 `-- --help` 查看。

## 经验与适用范围

- 实体管壁要连接内外壁和端环；实心杆不能保留贯穿内壁。透明材质不替代几何厚度。
- 延长双绞线需要延续螺距和相位，不能只把末端拉长。
- 圆截面需要垂直局部切线，使用平行运输标架可避免弯曲处压扁。
- 圆端面分别垂直各自切线，不等于两根端面在同一个全局平面。
- 边界边 0、非流形边 0、正体积不证明没有自相交或对象间穿模。
- 对象名可能保留旧颜色；以当前材质为准。避免用旧生成脚本覆盖已手工调整的场景。

历史最终文件名中的 `0p60` 是当时累计延长约 0.60 的版本标识，不自动代表毫米。完整从零生成的早期流程存在分支和路径缺口，本仓库提供经验与局部修复工具，不宣称能从任意 CAD 一键还原原始 V8 场景。

如需处理自己的模型，请提供参考图、实际 `.blend` 路径、要改的对象、保持的内容，以及是否允许清理旧版。大批历史清理应在最终文件重新打开、依赖检查和哈希核对后执行。
