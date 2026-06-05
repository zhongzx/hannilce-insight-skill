# 项目规则（本地开发）

## Python 版本

- 统一使用 Python >= 3.11
- 本仓库建议使用 `.venv` 虚拟环境（`python3.11 -m venv .venv`）

## 代码质量（Ruff）

- Ruff 安装在虚拟环境中，优先使用以下命令（避免系统 PATH 找不到 `ruff`）：
  - `./.venv/bin/ruff --version`
  - `./.venv/bin/ruff check .`
  - `./.venv/bin/ruff format --check .`
- 提交前必须通过：
  - `./.venv/bin/ruff check .`
  - `./.venv/bin/ruff format --check .`
- 如需自动修复格式：
  - `./.venv/bin/ruff format .`
  - `./.venv/bin/ruff check . --fix`

## 密钥与配置（必须遵守）

- 禁止在任何文件、提交记录、PR、Issue、日志中出现明文 API Key / Token
- OpenRouter 本地配置建议使用以下任一方式：
  - 环境变量：`OPENROUTER_API_KEY` / `OPENROUTER_MODEL`
  - 本地文件：`.openrouter.json`（已加入 `.gitignore`，禁止提交）
- 开启 OpenRouter 调用可见性（仅本机调试）：
  - 设置 `OPENROUTER_DEBUG=1`，会在 stderr 打印每次调用是否成功与耗时（不打印密钥）

## 本机交互式运行

- 交互式 REPL：
  - `PYTHONPATH="$PWD" ./.venv/bin/python mbti/insight_skill.py --repl <姓名>`
