# 聊天运行与测试

更新：2026-10-09。

## 配置和启动

macOS 执行 `./scripts/setup_macos.sh` 安装依赖，随后双击 `Start.command` 或运行 `./run.sh`。已有虚拟环境但缺少 OpenAI SDK 时，双击入口会重新执行安装。Linux 可在 Python 3.10+ 虚拟环境中执行 `pip install -r requirements.txt`，再运行 `./run.sh`。

复制 `.olivia.example.json` 为被 Git 忽略的 `.olivia.local.json`，在本地填写 api_key、base_url 和 model 三个必填项。三者一起作为机器本地配置，支持双击启动；代码和示例文件不包含实际值。

同一 shell 中的 ARK_API_KEY、ARK_BASE_URL、ARK_MODEL 可覆盖对应字段。未配置的字段不会回退到内置地址或模型；发送时会提示补全配置，并且不会发起模型请求。配置值不通过前端 JSON-RPC 发送，不要提交本地文件。

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
- 随时继续发送，包括完全不同的话题：前端始终只发送 text；backend 接收后自行调整未完回复。被替换草稿留在内部历史中并标记 superseded。
- 椭圆气泡随文字增长，短句保持紧凑，只有达到最大尺寸后才滚动。支持选中复制，正在阅读时暂停自动收起；完成后闲置 30 秒收起。卡片“对话”入口和右键“关闭对话 / 展开对话”统一切换显示。
- 气泡和右键菜单不提供停止按钮。用户通过继续说话来补充或修正；收起气泡和隐藏人物只改变显示。气泡不放关闭叉号，关闭/展开合并到右键菜单和卡片“对话”入口。
- 失败可通过右键“重试上次聊天”把上一条输入填回输入框，编辑后再发送；后台退出时用“重新连接聊天后台”。

## 自动化回归

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
```

包含原有姿势/眨眼/输入/启动器回归，以及：

- agents / messages 归属、父子关系约束、用户 generation 为空、按 agent 分页与上下文隔离；
- SQLite 事务状态、检查点、启动恢复、独占锁；跨回复与重启的 generation 单调递增；
- 连续 chat/send、旧草稿排除、用户原话保留、退出中断；完成前后发送使用同一契约；
- 真实子进程 JSONL 通信、错误请求、通知无需响应、重启恢复；
- QProcess + 模拟模型 + SQLite + 气泡全链路；旧展示消息片段过滤、连续发送、长文滚动、收起/展开、边缘位置、崩溃重连。

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

## 当前验证记录（2026-10-09）

本次持续对话协议与 agent 历史的离屏回归：51 项，49 通过，2 项原生 macOS 快捷键测试按环境跳过。测试包含真实 backend 子进程和 QProcess + SQLite + 气泡链路；模型使用确定性的 MockModel，不调用外部服务。

已验证：主 agent 固定为 1、父子关系与消息角色约束、各 agent 历史隔离、跨回复/重启版本递增、用户消息 generation 为 NULL、前端发送只有 text、输出不含 agent ID/generation、生成中与完成后的输入均能受理、旧展示消息的迟到片段过滤、流式完成前正文可见，以及原有气泡与人物交互回归。

本地模型配置覆盖：api_key、base_url 和 model 从同一文件读取，环境变量可覆盖且不回写文件；缺少配置时没有内置连接值，不创建模型客户端。

本次没有重跑原生窗口服务或真实 Ark 联测；模型 API 适配、人物素材和气泡几何未改变，人物输入回调只做统一发送与文本恢复。数据库创建从空库开始，没有迁移或兼容路径。Subagent 调度、后台工具和主动发言仍是后续工作，不在本次通过范围内。

## 上游流式与原生 UI 参考记录（2026-10-08）

此前使用合成输入和临时数据库完成真实 Ark backend、原生 Qt 流式/steer 联测。真实 SSE 对照样本：相同短消息和 endpoint，默认思考模式首正文约 24.83 秒，关闭思考后约 2.89 秒。该记录只说明关闭思考对首正文延迟的影响，不能作为固定延迟承诺，也不替代当前实现的原生复测。

关闭思考的原生 Qt 样本：首次输入约 3.83 秒到首正文，补充后的生成约 2.67 秒到首正文，收到 288 条增量后完成；流式期间气泡已显示内容。认证信息与内部思考内容未写入记录。

`scripts/render_bubble_previews.py` 可离线输出等待、短句、中段和长文的应用预览，不调用模型。尚未实测 Linux 原生桌面、多显示器热插拔、跨 Space / 全屏及接近上游上下文上限的超长历史。
