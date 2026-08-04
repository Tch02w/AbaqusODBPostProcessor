# Abaqus Workbench

本仓库是在保留两个原项目 Git 历史的基础上，将 AbaqusSubmitter 与
AbaqusODBPostProcessor 合并为一个 PySide6 桌面工作台的集成分支。

## 模块边界

- `src/abaqus_workbench/`：唯一桌面应用入口、主窗口和跨模块工作流；
- `components/AbaqusWorkbenchCore/`：路径、设置、进程事件、Abaqus 命令和 PySide6 基础设施；
- `components/AbaqusSubmitter/`：作业提交、调度、监测、远程连接和 ODB 合并；
- `components/AbaqusODBPostProcessor/`：ODB 检测、升级、批量提取、云图动画和结果浏览。

最终应用只创建一个 `QApplication`。两个业务组件不再拥有独立的主题或窗口绘制主线，
但仍保留各自的兼容启动入口，便于模块级回归测试。

提交器的“后处理”菜单可以把当前作业的 ODB 路径交给后处理页。该动作只切换目录，
不会自动启动可能耗时较长的 ODB 扫描；结果浏览也在同一个主窗口标签页中打开。

## Python 环境

项目根目录的 `.venv` 是唯一开发环境，当前使用 CPython 3.15。由于 3.15 尚处于预览期，
依赖同步时需要允许预发布版本：

```powershell
uv sync --all-packages --group dev --prerelease allow
```

安装完成后可通过无控制台的 GUI 入口启动：

```powershell
.\.venv\Scripts\abaqus-workbench.exe
```

工作台入口、后处理页面和可选绘图库采用 Python 3.15 的显式惰性导入。Matplotlib 在
当前 3.15 Windows 环境没有可用轮子时不是必需依赖，曲线输出自动使用 Pillow 后备实现。
Abaqus 自带解释器执行的 `noGUI` 脚本没有使用 3.15 语法，以保持 Abaqus 版本兼容性。

## 开发原则

1. 先拆分边界，再合并相似模块；
2. 求解调度与后处理队列保留各自领域模型，通过公共任务事件连接；
3. Abaqus `noGUI` 脚本继续与宿主界面依赖隔离；
4. 每一阶段同时运行两个组件的原有测试。
