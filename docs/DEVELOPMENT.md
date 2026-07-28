# 开发

## 环境

代码是纯 Python，跨平台。开发和测试在哪都行（GUI 测试跑 Qt 的 offscreen 后端，
不需要显示服务）；**打包必须在 Windows 上做**——PyInstaller 不能交叉编译。

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"        # Windows: .venv\Scripts\pip

.venv/bin/python -m pytest tests/         # 测试
.venv/bin/ruff check marginalia tests     # 检查
.venv/bin/ruff format marginalia          # 格式化
.venv/bin/python -m marginalia            # 直接运行
```

装本地 OCR（读扫描版需要）：

```bash
.venv/bin/pip install -e ".[ocr]"
```

## 在 WSL 里开发

WSLg 能显示 GUI，但字体渲染和高 DPI 缩放不如原生，长时间试读会别扭。建议：
**逻辑改动在 WSL 里跑测试，观感相关的改动到 Windows 上验。**

Windows 侧跑源码（不打包）：

```powershell
py -3.13 -m venv $env:USERPROFILE\.venvs\marginalia
& "$env:USERPROFILE\.venvs\marginalia\Scripts\pip" install -e "\\wsl.localhost\Ubuntu\home\jmz\dev\Marginalia[ocr]"
& "$env:USERPROFILE\.venvs\marginalia\Scripts\python" -m marginalia
```

虚拟环境刻意放在 Windows 本地盘而不是仓库里——仓库在 WSL 文件系统上，
把上百 MB 的 PySide6 装到 UNC 路径又慢又容易出问题。

## 目录

```
marginalia/
  app/        入口、配置、数据目录解析
  core/       PDF 文档、渲染线程、词框索引、配色
  store/      书库、笔记、进度、截图（全是文件，没有数据库）
  services/   OCR（可插拔后端）、LLM
  ui/         主窗、画布、侧栏、编辑卡
packaging/    PyInstaller spec、Inno Setup 脚本、构建脚本
tests/        pytest
docs/         设计文档
```

三个工程重心：

- `core/render.py` — 渲染线程与位图缓存。PyMuPDF 的 `Document` 非线程安全，
  渲染线程持有独立句柄；世代计数器负责丢弃快速滚动时积压的过期请求。
- `core/textmap.py` — 词框索引与拖选，全项目唯一有算法含量的地方。
- `ui/page_view.py` — 虚拟化画布，只渲染视口附近的页。

## 打包

**必须在 Windows 上做**，PyInstaller 不能交叉编译。

```powershell
py -3.13 -m venv $env:USERPROFILE\.venvs\marginalia-build
& "$env:USERPROFILE\.venvs\marginalia-build\Scripts\pip" install PySide6 PyMuPDF numpy pyinstaller pillow
# 想让安装包带上 OCR 组件，再装：
& "$env:USERPROFILE\.venvs\marginalia-build\Scripts\pip" install rapidocr-onnxruntime

& "$env:USERPROFILE\.venvs\marginalia-build\Scripts\python" packaging\build.py --all
```

产物在 `packaging/output/`：

| 文件 | 说明 |
|---|---|
| `Marginalia/` | PyInstaller 的 onedir 输出 |
| `Marginalia-x.y.z-Setup.exe` | 安装程序（需要 [Inno Setup 6](https://jrsoftware.org/isdl.php)） |
| `Marginalia-x.y.z-portable.zip` | 便携版，自带 `data\` |

几个容易踩的点：

- **onedir 不是 onefile。** onefile 每次启动都要解压两百多 MB，冷启动好几秒。
- **`marginalia.spec` 里有一长串 Qt 模块排除清单**，去掉之后体积大约减半。
  加新功能时如果用到了被排除的模块，源码运行一切正常、打完包才炸——所以务必跑自检。
- **OCR 是安装程序里的可选组件。** `build.py` 会把 OCR 相关文件挪到
  `output/ocr-component/`，安装程序据此分成两个组件。便携版则是全都带上。
- 安装程序的向导界面默认是英文（Inno 官方发行版不带简体中文）。把第三方的
  `ChineseSimplified.isl` 放进 Inno Setup 的 `Languages` 目录即可启用中文。

### 自检

打包后必须确认冻结的程序真的能跑：

```
Marginalia.exe --selftest
```

它会依次验证 Qt / PyMuPDF / numpy 能否加载、资源文件在不在、后台线程能否渲染页面、
高清裁剪、词框抽取、笔记落盘与回读。这些在源码运行时**全都正常**，只有打包后才会
暴露——而且失败时往往是一个没有任何输出的静默退出。

报告同时写到 stdout 和 `%TEMP%\marginalia-selftest.txt`（窗口程序没有控制台）。
`build.py` 会自动跑一遍，不过也可以手动跑。

## 发布

版本号只写在 `marginalia/__init__.py`，别处都从那儿读。发布流程：

```bash
# 1. 改版本号和 CHANGELOG
vim marginalia/__init__.py CHANGELOG.md
git commit -am "release: v0.2.0"

# 2. 打 tag 推上去，CI 自动构建
git tag v0.2.0
git push origin main v0.2.0
```

GitHub Actions 在 `windows-latest` 上构建（PyInstaller 不能交叉编译），跑自检，
产出安装程序和便携版，创建一个**草稿** Release。到 GitHub 上确认无误后手动发布。

CI 会核对 tag 和 `__version__` 是否一致——对不上直接失败，免得发出去版本号是错的包。

不打 tag 也可以手动触发 `发布` 工作流做一次构建（产物走 artifact，不建 Release），
用来验证打包链路没坏。

## 测试约定

- 所有涉及数据目录的测试用 `MARGINALIA_DATA_DIR` 指到 `tmp_path`，互不干扰
- GUI 测试用 `conftest.py` 里的 `qapp` / `pump` fixture；`pump(秒)` 转事件循环等后台线程
- 断言写「为什么」而不只是「是什么」——docstring 里说明这条性质为什么重要
