# v1.5.1

PDF_Bookmarks 是 Windows 本地 PDF 书签工具，可独立处理普通 PDF，也适配 Zotero 附件。支持文字/扫描目录识别、JSON 导入与编辑、预览校对、拖放和批量写入，保持原 PDF 的文件名与路径。

本次主要修复扫描目录漏章漏节，并规范自动生成的目录标题。面向 **Windows 10/11 x64**；Single 与 Portable GUI 功能相同，均无需安装 Python。

## 更新内容

1. **减少扫描目录漏项**：长点线可能干扰 OCR 的文字方向判断，把正常目录行误翻转180度。本版只对疑似目录比较两种方向结果，保留条目更完整的候选；不全局关闭方向纠正，保留倒置文字的处理路径。
2. **增强短标题识别**：在图像中找到连续点线后分离标题，使用原有本地模型重新识别，减少缺字、错字。拒绝截短原标题、低分、无关文字以及改变已有编号的候选；保留原页码和整行可信度，不因标题重读而把未重读页码标成高可信。识别继续离线运行，无新增依赖或在线 OCR。
3. **规范自动目录标题**：章/节编号与标题之间保留一个空格，例如 `第一章迭代与动力系统` → `第一章 迭代与动力系统`；省略数字节号前的 `§`，例如 `§1.1 迭代` → `1.1 迭代`。已识别章下的无编号直接子节补上 `章号.节号`，跨目录页继续编号并避开已有编号；补号仍需人工核对。
4. **修正书末条目层级**：译名对照表、关键词索引、习题答案等独立内容识别为一级目录，避免误归到最后一章、被补成章内节号。普通章内节标题继续按上下文处理。

## 兼容性与使用提醒

标题规范化只用于从 PDF 自动识别的新草稿；手工编辑、导入 JSON 和“复用已有 PDF 书签”的标题保留原样。JSON 格式、CLI 参数、页码映射证据、确认/保存、原路径写入、备份、校验及更新机制继续兼容。设置仍使用 `%LOCALAPPDATA%\ZoteroPDFBookmarks\settings.json`，`CONFIG_SCHEMA` 保持1，无新配置迁移。

识别出的条目仍是待校对草稿。模糊扫描、复杂双栏/多行目录、数学符号及缺页等可能需要手工修正；可信提示不是准确率保证，识别也不等于逐页验证正文。保存前请核对标题、层级、章首及末尾目标页，确认后再写入。

## 下载文件与区别

| 文件 | 用途与运行方式 |
| --- | --- |
| [PDF_Bookmarks_v1.5.1_win_x64.exe](https://github.com/Zerozero05/PDF_Bookmarks/releases/download/v1.5.1/PDF_Bookmarks_v1.5.1_win_x64.exe) | 单文件 GUI，直接运行、携带方便；启动时临时解包运行库 |
| [PDF_Bookmarks_v1.5.1_win_x64_portable.zip](https://github.com/Zerozero05/PDF_Bookmarks/releases/download/v1.5.1/PDF_Bookmarks_v1.5.1_win_x64_portable.zip) | 便携文件夹 GUI，完整解压后运行 `PDF_Bookmarks\PDF_Bookmarks.exe`；保留 `_internal`、`package-manifest.json` 和全部运行文件 |
| [PDF_Bookmarks_v1.5.1_win_x64_cli.exe](https://github.com/Zerozero05/PDF_Bookmarks/releases/download/v1.5.1/PDF_Bookmarks_v1.5.1_win_x64_cli.exe) | 命令行程序，适合脚本和批量处理 |
| [PDF_Bookmarks_v1.5.1_win_x64_full.zip](https://github.com/Zerozero05/PDF_Bookmarks/releases/download/v1.5.1/PDF_Bookmarks_v1.5.1_win_x64_full.zip) | 完整源码与程序包，含Single GUI、CLI、源码、测试、示例和许可；GUI入口 `PDF_Bookmarks\dist\PDF_Bookmarks.exe` |

Single 与 Portable 的 PDF 功能相同，区别主要是启动时是否临时解包；便携版移动时须保留整个文件夹，不承诺每页 OCR 或写入更快。两者共用原设置路径，“便携”指免安装，设置仍保存在当前电脑。

附件继续沿用 `PDF_Bookmarks_<版本>_<系统>_<架构>[_类型].<扩展名>`。另提供 [SHA256SUMS.txt](https://github.com/Zerozero05/PDF_Bookmarks/releases/download/v1.5.1/SHA256SUMS.txt)、[便携独立校验文件](https://github.com/Zerozero05/PDF_Bookmarks/releases/download/v1.5.1/PDF_Bookmarks_v1.5.1_win_x64_portable.zip.sha256) 和 [update-manifest.json](https://github.com/Zerozero05/PDF_Bookmarks/releases/download/v1.5.1/update-manifest.json)。前两项供下载校验，更新清单供GUI检查更新使用。

## 从旧版升级

v1.5.0 的 Single/Portable GUI 可通过“帮助 / 更新”升级，下载验证后由用户选择安装并重启；保留发行类型、用户改名后的入口和现有设置。请先保存目录编辑并等待识别、预览或写入结束。CLI/源码通过发布页手动更新。

v1.4.1 没有自动更新入口，首次升级须手动下载本版。Single运行新EXE；Portable完整解压新文件夹，不能只复制主EXE。旧程序中的PDF、目录JSON和其他自存文件自行保留。

## 验证

本次实际验证与局限记录在 [VALIDATION.md](https://github.com/Zerozero05/PDF_Bookmarks/blob/v1.5.1/VALIDATION.md)。扫描样例自动条目由34项恢复至39项（含“目录”入口），与视觉核对的参考相比，标题去除§和空白后匹配，层级/目标PDF页一致；这是具体样例结果，不保证所有PDF达到同等准确率。

本地完整源码回归327项全部通过；本版四类程序的资源/身份检查、临时PDF命令行写入及两种GUI成功升级/失败回滚的4个冻结场景通过。完整冻结GUI的真实书籍OCR交互尚未验证，识别效果结论来自上述只读源码样例。

正式标签触发的Windows构建须通过完整源码回归、四种构建身份/资源/许可核验、包校验和真实冻结更新/回滚验收，失败不发布。运行结果以 [Actions](https://github.com/Zerozero05/PDF_Bookmarks/actions/workflows/windows-build.yml) 为准。

[完整使用说明](https://github.com/Zerozero05/PDF_Bookmarks/blob/v1.5.1/docs/USAGE.md) · [版本记录](https://github.com/Zerozero05/PDF_Bookmarks/blob/v1.5.1/CHANGELOG.md)

沿用 GNU AGPL v3，第三方许可随完整包和便携包提供；历史版本、标签和资产保持不变。
