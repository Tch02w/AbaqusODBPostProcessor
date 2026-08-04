# AbaqusSubmitter

AbaqusSubmitter 是单机 Abaqus 作业提交、调度和运行监控工具。项目仅保留 Qt 主线，应用代码统一位于 `abaqus_submitter/`。

## 运行

```powershell
uv sync --group dev
uv run python -m abaqus_submitter
```

也可以运行根入口：

```powershell
uv run python .\abaqussubmit.py
```

运行时配置、队列和调度数据库保存在 `%LOCALAPPDATA%\AbaqusSubmitter\`。测试可通过 `ABAQUS_SUBMITTER_DATA_DIR` 指定隔离目录。

## 验证

```powershell
uv run ruff check abaqus_submitter tests
uv run python -m unittest discover -s tests -p "test_*.py"
uv run python tests\smoke_test_imports.py
```

## 打包

```powershell
uv run pyinstaller .\packaging\AbaqusSubmitter.spec --noconfirm
```

架构决策见 `docs/adr/`，领域语言与开发方向见 `CONTEXT.md`。
