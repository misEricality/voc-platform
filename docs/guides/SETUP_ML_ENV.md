# 搭建 ML 环境（torch + sentence-transformers）

> 用途：在本地准备一个能运行 bge 语义向量化（embedding / 聚类）的环境。
>
> 关联：`src/analyzers/embedder.py` · `scripts/ops/backfill_embeddings.py` · `scripts/dev/l35_cluster.py`

## 0. 环境已就绪：给其他 Agent / 会话的执行须知（2026-08-16）

> 本环境已搭建完成并端到端验证通过（`src.analyzers.embedder` 已能加载 bge-small-zh-v1.5 完成向量化）。
> 其他 Agent / 会话在本机跑 torch 脚本时，**不要再执行下文第 1~5 步**，直接按本节使用。

### 0.1 唯一正确的运行方式

```powershell
# 方式 A（推荐）：直接调绝对路径，不激活、不依赖 PATH
D:\projects\voc_platform\.venv-ml\Scripts\python.exe -c "from src.analyzers.embedder import get_embedder; e=get_embedder(); print('OK', e.model_name, e.dim)"

# 方式 B：激活后使用
cd D:\projects\voc_platform
.\.venv-ml\Scripts\Activate.ps1
python -c "from src.analyzers.embedder import get_embedder; e=get_embedder(); print('OK', e.model_name, e.dim)"
```

- **禁止**用裸 `python`、`py -3.12`、`py` 跑本环境脚本——它们指向 3.14/其他解释器，torch 不可用或行为不一致。
- 脚本自身会处理 `sys.path`（从项目根导入 `src.*`），**必须从项目根目录（`D:\projects\voc_platform`）运行**，不要在别的目录直接执行脚本路径。

### 0.2 环境事实（已装好，勿动）

| 项 | 值 |
|---|---|
| 解释器 | `.venv-ml\Scripts\python.exe`（Python 3.12.13，uv 管理） |
| torch | **2.13.0+cpu**（CPU 版，满足 `torch>=2.1.0`，**不要再装/重装**） |
| sentence-transformers | 5.7.0 |
| transformers | 5.15.0 |
| 其余依赖 | `requirements.txt` 全量装齐（含 pandas 3.0.5 / streamlit 1.61.1 / jieba 等） |
| bge 模型 | 已缓存于 `C:\Users\44481\.cache\huggingface\hub\models--BAAI--bge-small-zh-v1.5`，运行时不会重新下载 |

### 0.3 本机环境的硬性禁令（违反会破坏环境）

1. **不要 `pip install --upgrade pip` / `pip uninstall`**：本机沙箱回收站不可用，升级/卸载会报 `SAFE_DELETE_FAIL_CLOSED` 并可能损坏 venv 内 pip（已发生过一次）。
2. **不要重装 torch**：PyPI 默认源是 CUDA 版；环境内已是 CPU 版且满足全部依赖约束，重装会引入 CUDA 依赖或破坏现状。新装包若依赖解析到 torch，确认其版本约束 ≤ 2.13 即可，无需升级。
3. **新装包的正确方式**（二选一）：
   ```powershell
   # 方式 A：uv（推荐，不走沙箱回收站）
   uv pip install --python D:\projects\voc_platform\.venv-ml\Scripts\python.exe <pkg>

   # 方式 B：pip（若遇 SAFE_DELETE_FAIL_CLOSED，须在沙箱外执行）
   D:\projects\voc_platform\.venv-ml\Scripts\python.exe -m pip install <pkg>
   ```
4. **不要动 `.venv-review`**：它是 3.14 环境，装 torch 会失败；两个环境职责分离。

### 0.4 运行须知

- 首次运行会打印 `unauthenticated requests to the HF Hub` 警告——因未设 `HF_TOKEN`，**可忽略**（模型在本地缓存，不会联网下载）。
- 上面的 `-c` 验证**只读**：仅加载模型并打印模型名/维度，不改数据库、不写任何文件。
- 若需跑其他 ML 脚本（如 `scripts/ops/backfill_embeddings.py`），先看其是否调用 `src.analyzers.embedder`——该模块已由本环境支撑，但**回填操作会写数据库，执行前先向工程师确认**。

---

## 为什么需要单独的环境

本项目当前开发机默认 Python 为 **3.14**，而截至本文撰写时，PyTorch 尚未发布稳定的 `cp314` wheel。
直接 `pip install torch` 会退回到源码编译，在 Windows 上往往**静默失败**。

因此建议：**安装 Python 3.12，并新建一个独立的 ML 虚拟环境（`.venv-ml`）**，不要污染现有的 `.venv-review`（基于 3.14）。

## 前置检查

```powershell
python --version      # 确认当前默认版本
py -0p                # 列出已安装的 Python 版本
```

如果列表里没有 `3.12`，先执行下一步安装。

## 1. 安装 Python 3.12

任选其一：

```powershell
# 方式 A：winget（推荐）
winget install -e --id Python.Python.3.12
```

或手动下载：

> <https://www.python.org/downloads/release/python-3129/>
>
> 安装时勾选 “Add python.exe to PATH”。

安装后确认：

```powershell
py -0p
# 应能看到 -V:3.12 *
```

## 2. 新建 ML 虚拟环境

```powershell
cd D:\projects\voc_platform

# 用 3.12 建一个专门跑 embedding 的环境
py -3.12 -m venv .venv-ml
.\.venv-ml\Scripts\Activate.ps1
```

## 3. 安装 CPU 版 PyTorch

> 本项目只用 `bge-small-zh-v1.5` 做本地推理，CPU 足够，且体积远小于 CUDA 版。

```powershell
python -m pip install --upgrade pip
python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
```

务必带 `--index-url .../cpu`，避免拉到错误的版本或触发源码编译。

## 4. 安装 sentence-transformers

```powershell
python -m pip install "sentence-transformers>=2.7" "transformers>=4.36.0"
```

## 5. 安装项目其余依赖

```powershell
python -m pip install -r requirements.txt
```

如果安装 `requirements.txt` 时又触发重装 torch，可改用：

```powershell
python -m pip install -r requirements.txt --no-deps
```

## 6. 验证安装

```powershell
python -c "import torch, sentence_transformers, transformers; print('torch', torch.__version__); print('sentence-transformers', sentence_transformers.__version__)"
```

## 7. 验证 bge 语义向量化

```powershell
.\.venv-ml\Scripts\Activate.ps1

# 加载本地 bge 模型并打印模型名/维度（只读，验证 ML 环境与向量化链路可用）
python -c "from src.analyzers.embedder import get_embedder; e=get_embedder(); print('OK', e.model_name, e.dim)"
```

预期输出类似 `OK BAAI/bge-small-zh-v1.5 512`，表示 ML 环境与向量化链路可用。

## 注意事项

1. **模型已缓存**：`BAAI/bge-small-zh-v1.5` 通常已在 `C:\Users\<用户>\.cache\huggingface\hub` 里，不会重复下载；若缓存缺失，首次运行会自动联网下载约 95MB。
2. **不要使用 `.venv-review`**：它是 Python 3.14 环境，装 torch 会失败。
3. **CPU 即可**：bge-small-zh-v1.5 单条评论向量化在 CPU 上毫秒级完成，无需 CUDA。
