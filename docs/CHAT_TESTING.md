# 聊天运行与测试

更新：2026-10-08。

## 配置和启动

macOS 执行 `./scripts/setup_macos.sh` 安装依赖，随后双击 `Start.command` 或运行 `./run.sh`。已有虚拟环境但缺少 OpenAI SDK 时，双击入口会重新执行安装。Linux 可在 Python 3.10+ 虚拟环境中执行 `pip install -r requirements.txt`，再运行 `./run.sh`。

配置方式任选：

1. 在启动桌宠的同一个 shell 设置 `ARK_API_KEY`；可选 `ARK_BASE_URL`、`ARK_MODEL`。
2. 将 `.olivia.example.json` 复制为 `.olivia.local.json`，在本地填写 Key。该文件被 Git 忽略，支持双击启动。不要把 Key 写进人设、方案文档或源码。

环境变量优先于本地配置。默认使用用户指定的北京方舟地址和 `ep-20260624173305-44d54`。Key 由 backend 调用 SDK 时读取，不通过 JSON-RPC 发送。

其他配置：

| 环境变量 | 本地 JSON 字段 | 默认 |
| --- | --- | --- |
| `OLIVIA_CONFIG` | — | 项目根目录 `.olivia.local.json` |
| `OLIVIA_DB_PATH` | `database` | 系统用户数据目录 `chat.sqlite3` |
| `OLIVIA_PROMPT_PATH` | `prompt` | `prompts/olivia.md` |
| `OLIVIA_BUBBLE_SECONDS` | `bubble_seconds` | 30，允许 1–3600 秒 |
| `ARK_THINKING` | `thinking` | `disabled`；支持 `enabled` / `auto` |
| `OLIVIA_PROVIDER` | `provider` | `ark`；`mock` 仅用于离线测试 |

不需要单独启动 backend；桌宠负责启动和退出。第二个桌宠实例不能同时使用同一历史库，测试时应始终指定临时数据库。

## 交互

- 输入后 Enter 或箭头发送。回复以文字流显示在人物旁边的气泡里。
- 回复期间继续发送：补充当前输入，取消旧生成，从头展示新回复。旧草稿被保留在数据库中并标记 superseded。
- 椭圆气泡随文字增长，短句保持紧凑，只有达到最大尺寸后才滚动。支持选中复制，正在阅读时暂停自动收起；完成后闲置 30 秒收起。卡片“对话”入口和右键“关闭对话 / 展开对话”统一切换显示。
- 气泡和右键菜单不提供停止按钮。用户通过继续说话来补充或修正；收起气泡和隐藏人物只改变显示。气泡不放关闭叉号，关闭/展开合并到右键菜单和卡片“对话”入口。
- 失败可通过右键“重试上次聊天”把本轮输入填回输入框，编辑后再发送；后台退出时用“重新连接聊天后台”。

## 自动化回归

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
```

包含原有姿势/眨眼/输入/启动器回归，以及：

- SQLite 事务状态、检查点、启动恢复、分页、独占锁；
- 多次 steer、旧草稿排除、历史上下文、取消、流中断；
- 真实子进程 JSONL 通信、错误请求、通知无需响应、重启恢复；
- QProcess + 模拟模型 + SQLite + 气泡全链路；旧版本片段过滤、快速补充、长文滚动、收起/展开、边缘位置、崩溃重连。

原生 macOS 桌面验证：

```bash
QT_QPA_PLATFORM=cocoa .venv/bin/python -m unittest discover -s tests -v
```

原生测试还覆盖 macOS 快捷键注册/冲突/释放。需要能够访问 macOS 窗口服务的本地终端，受限沙箱里无法连接 WindowServer 的失败不代表应用逻辑失败。

## Backend 总体测试脚本

```bash
# 离线：真实 backend 子进程 + 模拟模型
.venv/bin/python scripts/test_backend.py

# 真实 Ark：两轮短测试，第二轮在重启 backend 后验证上下文
.venv/bin/python scripts/test_backend.py --live
```

脚本始终使用临时数据库；`--live` 才使用配置的 Key 发起真实请求。不读取或上传正式聊天历史，输出通过/失败结果，不打印 Key 或完整模型请求。

## 带 UI 联合冒烟与预览

```bash
QT_QPA_PLATFORM=cocoa .venv/bin/python scripts/test_chat_ui.py
QT_QPA_PLATFORM=cocoa .venv/bin/python scripts/test_chat_ui.py --live
```

脚本打开测试人物窗口，在首段输出后自动追加输入，验证重新生成和完成，保存人物与气泡的应用内渲染预览，然后关闭窗口。默认输出到 `output/previews/chat.png`，可用 `--output PATH` 更改。该预览组合的是应用控件，不截取桌面上的其他应用。

## 本次验证记录

2026-10-08，macOS / Python 3.13 / PyQt5 5.15.11 / OpenAI SDK 2.8.1：

- 首版离屏回归：38 项，36 通过，2 项原生热键测试跳过。
- 首版原生 Cocoa 回归：38 项全部通过，包含额外的上游错误/截断处理和大历史分页测试。
- 真实 Ark backend 冒烟：流式输出、SQLite 持久化、重启后的第二轮上下文通过。
- 真实 Ark + 原生 Qt 联合冒烟：发送、持续输出、steer 重新生成、最终气泡一致性通过；预览位于 `output/previews/chat-live.png`，已视觉检查。

离线模型用于确定性错误和竞态场景，不能替代上游服务验证。尚未实测 Linux 原生桌面、多显示器热插拔、跨 Space / 全屏，以及超长历史接近上游上下文上限的表现。当前没有历史浏览界面，历史查询只提供 backend RPC。

## 气泡与首段延迟修订（2026-10-08）

增加自适应椭圆、无停止或关闭按钮、右键/卡片统一关闭展开、正文完成前可见的回归测试。`scripts/render_bubble_previews.py` 可离线输出等待、短句、中段和长文四种应用预览；不调用模型。

真实 SSE 对照样本：相同短消息、相同 endpoint，默认模式 HTTP 200 / text/event-stream，133 个思考片段、64 个正文片段，首事件约 6.99 秒、首正文约 24.83 秒；关闭思考后 34 个正文片段、首正文约 2.89 秒、总计约 3.73 秒。只记录时间和计数，不记录认证信息或思考内容。两次请求的负载和网络情况会影响结果，不代表固定性能。

修订后原生 Qt 联测（关闭深度思考）：首次输入约 3.83 秒到首段正文，补充后的第二版约 2.67 秒到首段正文，收到 288 条增量通知后完成；流式期间已更新气泡文本。以上均为合成对话和临时数据库。

最终修订验证：离屏 41 项（39 通过，2 项原生热键跳过），原生 Cocoa 41 项全部通过。关闭/展开菜单随气泡状态切换，卡片入口行为一致；关闭后的流式内容继续接收且完成时不重新弹出。等待、短句、中段和长文预览已检查，气泡中没有按钮。
