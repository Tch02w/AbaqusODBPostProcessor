# 编码安全规则

1. 项目中的文本文件必须保持 UTF-8 编码。
2. 禁止将源码保存为 ANSI、GBK、UTF-16 或其他编码。
3. 中文注释、中文 UI 文本、中文日志和状态字符串不得发生非任务要求的变化。
4. 优先使用 apply_patch 或局部编辑，不要为了少量修改重写整个文件。
5. 禁止使用未显式指定 UTF-8 的 PowerShell 命令重写源码：
   - Set-Content
   - Out-File
   - >
   - >>
   - Add-Content
6. 如果必须整文件写入，使用 Python 并显式指定：
   encoding="utf-8"
7. 修改后检查是否出现疑似乱码：
   - 瀹
   - 缁
   - 锛
   - 鍚
   - �
   - ????
8. 发现疑似乱码时不要擅自修改。 继续完成当前任务，并在最终总结中报告文件路径、行号和对应文本片段。
9. 在执行 git add、git commit、git reset、git restore、git checkout、git stash 或 git clean之前需要询问。

## Agent skills

### Issue tracker

Issues are tracked in GitHub Issues for `https://github.com/Tch02w/AbaqusSubmitter.git`. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the default five-label triage vocabulary. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context repository: read root `CONTEXT.md` and `docs/adr/`. See `docs/agents/domain.md`.

