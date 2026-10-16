# 运行与验证

本文面向运行应用和修改代码的贡献者，说明本地配置、测试入口及验证边界。系统职责见 [Agent 架构](AGENT_ARCHITECTURE.md)，接口与数据契约见 [通信协议](CHAT_PROTOCOL.md) 和 [历史与记忆](HISTORY_MEMORY.md)。

## 配置和启动

macOS 执行 `./scripts/setup_macos.sh` 安装依赖，随后双击 `Start.command` 或运行 `./run.sh`。已有虚拟环境但缺少 OpenAI SDK 时，双击入口会重新执行安装。Linux 可在 Python 3.10+ 虚拟环境中安装 `requirements.txt`，再运行 `./run.sh`。

复制 `.olivia.example.json` 为被 Git 忽略的 `.olivia.local.json`，填写 api_key、base_url 和 model。三者均为必填，不使用内置服务地址或 endpoint；配置不完整时不会发起模型请求。不要提交本地配置。

| 环境变量 | 本地 JSON 字段 | 默认值或含义 |
| --- | --- | --- |
| `OLIVIA_CONFIG` | — | 项目根目录 `.olivia.local.json` |
| `ARK_API_KEY` | `api_key` | 必填的模型服务凭据 |
| `ARK_BASE_URL` | `base_url` | 必填的服务地址 |
| `ARK_MODEL` | `model` | 必填的模型或 endpoint |
| `OLIVIA_DB_PATH` | `database` | 系统用户数据目录中的 `chat.sqlite3` |
| `OLIVIA_PROMPT_PATH` | `prompt` | `prompts/olivia.md` |
| `OLIVIA_BUBBLE_SECONDS` | `bubble_seconds` | 30 秒，允许 1–3600 |
| `ARK_THINKING` | `thinking` | 直接适配器调用的默认值；运行时按角色选择思考设置 |
| `OLIVIA_WORKSPACE` | `workspace` | 项目目录；相对路径基于配置文件目录 |
| — | `shell_enabled` | false；显式开启后允许子 agent 执行任意 shell |
| `OLIVIA_PROVIDER` | `provider` | `ark`；`mock` 用于离线测试 |
| — | `context_token_budget` | 64000，允许 8000–1000000 |
| — | `memory_interval_seconds` | 60 秒，允许 1–3600 |
| — | `memory_threshold_bytes` | 16000，允许 1000–1000000 |
| — | `memory_recent_messages` | 12 条，允许 1–100 |

环境变量覆盖对应本地字段，不回写配置。主 agent 固定关闭思考，业务子 agent 和记忆加工开启思考。

文件工具将路径限制在 workspace 内，shell 使用当前用户权限，不受该目录边界限制。开启 shell 不会建立 OS 沙箱或命令审批流程。

### 数据位置与开发环境

macOS 默认数据库位于 `~/Library/Application Support/Olivia Lin Fan Pet/chat.sqlite3`；Linux 位于 `${XDG_DATA_HOME:-~/.local/share}/olivia-desktop-pet/chat.sqlite3`。记忆目录为数据库旁的 `<数据库文件名>.memory/`，由 CURRENT 指向完整发布版本。

原始历史始终保留，旧记忆版本暂不自动清理。备份应在 backend 停止后同时保存数据库和记忆目录，恢复到原数据库路径；绑定包含数据库绝对路径，移动或重命名不会自动重绑定。不要直接编辑已发布版本，文件校验失败会阻止加载。

同一数据库只允许一个 backend 写入。开发与测试使用独立临时数据库，不连接正式历史。项目不提供旧结构迁移；遇到不兼容的开发库，应先备份再选择新路径，程序不会自动清空历史。

### 容量与超时

| 范围 | 限制 |
| --- | --- |
| 一次 agent turn | 最多 12 次模型调用；主/子执行时限分别为 300/600 秒 |
| 主对话容量等待 | 每轮累计最多 370 秒，与执行时限分开 |
| 记忆加工 | 每批目标 16000 字节，每次最多 16 次模型调用、180 秒，最多尝试 2 次 |
| 加工触发 | 有积压时按 memory_interval_seconds 定时；达到 memory_threshold_bytes 提前触发；失败后至少间隔 60 秒后台重试 |
| 页面容量 | 普通页最多 16000 字节，hot.md 和根 index.md 各最多 6000 字节，最多 1000 个 Markdown 文件 |
| 原文读取 | 单次最多 20 条、24000 字节；每轮记忆读取累计最多 32000 字节 |
| shell | 默认 60 秒时限，输出最多 64 KiB |

context_token_budget 包含 messages 和 tools，使用序列化 JSON 的 UTF-8 字节数作保守估算，不是模型精确 token 数。配置时需为上游输出和协议开销留余量。近期已加工窗口可缩小，未加工的有效对话不会因预算不足而静默丢弃。

## 自动化测试

在项目根目录运行完整离屏回归：

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
```

按修改范围也可以运行专项入口：

```bash
# 真实 backend 子进程、模拟模型和临时 SQLite
.venv/bin/python scripts/test_backend.py

# 子 agent、工具历史、结果回流与 timer
.venv/bin/python scripts/test_agents.py

# 记忆加工、发布恢复、上下文容量与并发输入
.venv/bin/python scripts/test_memory.py
```

| 测试位置 | 主要覆盖 |
| --- | --- |
| `tests/test_agent_history.py`、`test_agent_tool_messages.py` | agent 归属、turn、事务、工具配对、稳定键与未知结果 |
| `tests/test_agent_runtime.py`、`test_agent_result_timer.py` | 委派、补充、取消、后台执行、主动回流、暂缓与输入竞争 |
| `tests/test_memory.py`、`test_memory_compiler.py` | 增量内存视图、固定批次、发布恢复、读取与容量等待；加工回执、读后修改、入链维护、拆分合并及来源约束 |
| `tests/test_backend.py`、`test_backend_transport.py` | 连续输入、JSONL、流式通知、进程退出与重启 |
| `tests/test_ark_model.py`、`test_config.py` | 模型流解析、错误分类、工具调用和配置校验 |
| `tests/test_chat_ui.py` | QProcess 到气泡的离屏链路、迟到片段、阅读、隐藏与展开 |
| `tests/test_pet_interactions.py`、`test_mac_hotkey.py`、`test_launcher.py` | 人物交互、快捷键和启动器 |

确定性测试使用可控模型决定工具选择和竞争时序；文件、进程和数据库按各测试实际执行。它们验证调度与契约，不测量模型理解自然语言的准确率。

## 真实模型冒烟

以下命令使用本地模型配置发起真实请求，只构造合成对话和临时历史，不读取正式聊天数据库：

```bash
# 两轮短对话，中间重启 backend
.venv/bin/python scripts/test_backend.py --live

# 委派本地工作，执行中追加闲聊，等待结果报告
.venv/bin/python scripts/test_agents.py --live

# 委派后不再输入，验证后台结果主动唤醒
.venv/bin/python scripts/test_agents.py --live --no-followup

# 加工 Markdown，重新加载，按页查阅并核对 raw
.venv/bin/python scripts/test_memory.py --live
```

agent 冒烟在临时目录执行真实 shell 并检查文件产物、子执行状态、结果投递和流式输出。记忆冒烟检查带来源的页面、磁盘重载、read_memory / read_raw 调用以及回答正确性。脚本输出通过/失败信息，不打印 API key 或完整模型请求。

## 记忆维护评估

```bash
.venv/bin/python scripts/eval_memory.py --live
```

该脚本使用本地模型配置，在临时数据库中连续加工三批合成资料：用户沟通偏好、项目与负责人及事件；人物别名、改期和助手猜测；偏好纠正和项目结束。随后重载文件、沿索引查页并核对原话，再验证停用来源仍有原始留底。

程序检查稳定页面复用、跨分类链接、当前信息与历史变化、hot 更新、来源停用及召回答案中的必要事实。原文核对还要求实际调用 read_raw 并成功返回消息，不能以页面带来源链接或答案正确代替读取。输出保存在被 Git 忽略的 output/reviews/，包含各批 Markdown、合成原文、模型请求/工具结果及报告，便于检查误归纳和遗漏。运行失败不计为语义通过；关键词检查也不能证明整段答案正确，须复核否定、时间条件及引用依据。

可控模型的拆分/合并测试证明文件协议和导航可以完成相应操作，不证明真实模型能在复杂资料中自行做出正确划分。长期、多主题的准确率、稳定性和成本需要独立样本集评估。

## 原生 UI 与预览

原生 macOS 验证需要能够访问 WindowServer 的本地终端：

```bash
QT_QPA_PLATFORM=cocoa .venv/bin/python -m unittest discover -s tests -v
QT_QPA_PLATFORM=cocoa .venv/bin/python scripts/test_chat_ui.py
QT_QPA_PLATFORM=cocoa .venv/bin/python scripts/test_chat_ui.py --live
```

原生测试包含快捷键注册、冲突与释放。受限环境无法连接窗口服务时，不能把环境失败解释为应用行为失败，也不能以离屏通过替代原生验证。

UI 冒烟打开测试人物窗口，在首段正文后追加输入，检查重新生成和完成，再保存应用控件渲染预览。默认路径为 `output/previews/chat.png`，可用 `--output PATH` 修改；它不截取其他桌面应用。

手工检查应覆盖：生成期间持续输入、短句气泡紧凑、长文达到上限后滚动、阅读期间不抢占内容、手动关闭后后台结果不重新展开，以及人物素材和几何保持稳定。

## 验证覆盖与边界

已有离屏回归覆盖文本唯一入口、流式展示、主子历史隔离、单调 turn、操作去重、结果恢复和记忆读写闭环。真实 backend 子进程、Qt 离屏集成和临时数据库重启均有测试入口。

记忆回归专门覆盖事务回滚不污染内存、晚完成消息不遗漏、固定批次期间继续输入、前台取消不取消共享加工、后台加工不占业务槽、只恢复最新有效轮次，以及指针切换前后失败时的版本一致性。纠正与停用验证指定来源不从旧快照、工具返回或后续加工中复活，原文仍保留。

已有真实模型样本通过委派与并行接话、无后续输入的主动结果报告，以及文件记忆加工、重载、查页、原文核对和流式回答。这些是链路冒烟，不能据此推断复杂场景质量：

| 尚需专门验证的范围 | 现有证据的边界 |
| --- | --- |
| 补充、撤回和多目标指代的语义准确率 | 可控模型验证运行时行为，真实样本未形成系统评测 |
| 长期事实修订、矛盾消解和跨页关联 | 三批合成场景有通过样本；通过结构测试或一次召回不代表长期语义质量，尚无多主题长期质量数据 |
| 多问题召回时的原文核对 | 已观察到模型只读页面却声称核对原话；此时 read_raw 检查判失败。来源 ID 和提示规则不能保证模型继续读取 |
| 大规模积压的延迟、调用成本与 token 使用 | 有容量和等待边界测试，没有规模化基准 |
| 文件系统断电耐久性 | 发布异常注入及协程取消已有覆盖，未做真实断电实验 |
| 外部动作的崩溃一致性 | 调用凭据保留未知结果，不提供跨进程外部副作用 exactly-once 保证 |
| 原生桌面和全局快捷键 | 需要原生环境测试，离屏执行会跳过相关项目 |

评估连续对话时，应同时观察前台首正文延迟、后台完成质量、补充应用延迟、重复执行率、结果报告延迟和模型开销。
