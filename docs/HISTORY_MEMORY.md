# Agent 历史与记忆接口

状态：按 agent 归属的 SQLite 历史、独立生成版本与全量有效历史上下文已实现。更新：2026-10-09。

Subagent 的运行、工具循环和主动结果回流尚未实现；本次完成其所需的身份与历史基础。架构选择及原因见 [Agent 设计](AGENT_ARCHITECTURE.md)。

## 为什么只保留 agent 和消息

用户与 Olivia 的主对话持续存在，不按每次回复建立新 chat。主 agent 固定为 1，subagent 通过 `parent_agent_id` 关联父 agent；无需 kind 字段。每个 agent 的消息归入自己的历史，第一版一项后台工作对应一个 subagent，后续直接用 agent ID 做 update、cancel 和查询，不另设 taskId、executionId 或 contextId。

`agent_id` 管归属，`role` 管消息角色，消息 `id` 标识具体记录，`generation` 过滤被替换的生成。generation 不表示一轮用户输入与模型回复，不承担对话 ID 的职责。

## 数据归属和位置

历史唯一读写方是 backend；当前 UI 只接收对话正文，不查询内部 agent 历史。默认路径：

- macOS：`~/Library/Application Support/Olivia Lin Fan Pet/chat.sqlite3`
- Linux：`${XDG_DATA_HOME:-~/.local/share}/olivia-desktop-pet/chat.sqlite3`

`OLIVIA_DB_PATH` 可覆盖路径。SQLite 使用 WAL、外键和事务；旁边的 `.lock` 文件以进程文件锁保证单个 backend 写入。第二个实例拒绝启动，锁随进程退出释放。

首次启动创建以下结构，后续启动保留历史与 generation。

## Schema

### agents

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `id` | INTEGER PRIMARY KEY AUTOINCREMENT | Agent 身份；主 agent 固定为 1，其他 ID 不复用 |
| `parent_agent_id` | INTEGER NULL REFERENCES agents(id) | 主 agent 为 NULL，其余指向已存在的父 agent |
| `source_message_id` | TEXT NULL REFERENCES messages(id) | 创建原因的来源消息引用，不决定生命周期 |
| `status` | TEXT NOT NULL | idle / queued / running / waiting / cancelling / cancelled / completed / failed / interrupted |
| `generation` | INTEGER NOT NULL DEFAULT 0 | 本 agent 的当前生成版本，持久化、单调递增 |
| `created_at`, `updated_at` | TEXT NOT NULL | UTC ISO 8601，毫秒精度 |

数据库约束主 agent 为唯一根；子 agent 的父 ID 必须小于自己的 ID，防止自引用与环。结构支持将来递归；当前只实现历史 owner 的内部创建方法，没有对模型开放 subagent 创建或调度。

主 agent 每次回复生成时处于 running，生成结束后回到 idle；失败原因保存在对应消息上。启动时遗留 running 变为 interrupted，保留 generation。其后可开始新回复。子 agent 的取消等状态为后续执行架构预留，当前没有相关运行接口。

### messages

| 字段 | 类型 | 含义 |
| --- | --- | --- |
| `seq` | INTEGER PRIMARY KEY AUTOINCREMENT | 稳定排序与分页游标 |
| `id` | TEXT UNIQUE NOT NULL | UUID hex，标识具体消息 |
| `agent_id` | INTEGER NOT NULL REFERENCES agents(id) | 历史归属 |
| `role` | TEXT NOT NULL | user / assistant / tool；当前运行链路产生 user 和 assistant |
| `content` | TEXT NOT NULL | 原始输入或累积正文 |
| `generation` | INTEGER NULL | user 必须为 NULL；assistant 必须为正整数；tool 可记录来源版本 |
| `status` | TEXT NOT NULL | completed / streaming / superseded / failed / interrupted |
| `source_message_id` | TEXT NULL REFERENCES messages(id) | 为交接、补充、结果引用预留 |
| `model` | TEXT NULL | 生成消息使用的 endpoint / 模型标识 |
| `error_code` | TEXT NULL | 可分类错误码，不保存原始异常或认证信息 |
| `usage_json` | TEXT NULL | 上游实际用量，无数据为 NULL，不推算 |
| `created_at`, `updated_at` | TEXT NOT NULL | UTC ISO 8601，毫秒精度 |

索引为 `messages(agent_id, seq)`。System prompt 是配置，不作为 user 消息存储。后续工具调用与结果还需要 tool_call_id 等消息附属数据，当前不实现工具协议，不宣称已支持完整工具历史重放。

## generation 与消息角色

主 agent 每次新回复或 steer 都递增自己的 generation。不会在新输入到来时重新从 1 开始，重启也不重置。各 agent 分别计数；主 agent 的生成版本变化不会自动影响子 agent。

| id | agent_id | role | content | generation | status |
| --- | --- | --- | --- | --- | --- |
| m1 | 1 | user | 帮我整理会议 | NULL | completed |
| m2 | 1 | assistant | 好，我先…… | 10 | superseded |
| m3 | 1 | user | 产品那场别算了 | NULL | completed |
| m4 | 1 | assistant | 好，产品那场我就不算了…… | 11 | completed |

这是历史结构示例，不表示会议整理工具已经实现。有效上下文保留 m1、m3、m4，排除旧草稿 m2。m1、m3 不靠 generation 与某条回复配对。

未来 subagent 的执行同样使用本 agent 的 generation。一个生成过程可包含多条模型/工具消息；每条消息仍有独立 ID。版本过期阻止旧生成写入当前草稿，但不能删除已经完成的历史或已发生的外部操作。

## 写入、过滤与恢复

1. 收到 chat/send：backend 根据当前是否有未完草稿选择内部开始或替换流程。事务保存 completed 用户消息、递增主 agent generation、创建 streaming assistant 草稿，再确认受理；前端始终只提交 text。
2. 流式输出：正文在内存累积，约每 250 ms 写入草稿检查点，不为每个 token 创建消息。
3. 内部替换：终止旧模型流，将旧草稿标记 superseded，保存新输入、递增该 agent generation 并创建新草稿。事务外发送新的展示消息通知；不把 generation 或替换控制交给前端。
4. 完成：按 agent、generation、消息 ID 和 streaming 状态校验，保存全文、状态与用量；提交后通知 UI。迟到旧版本不能覆盖新生成，重复完成不重复写入。
5. 失败或内部取消：保留部分正文为 failed/interrupted。有效用户输入仍保留在持续对话上下文中；不再因为某条回复失败而丢弃该次用户输入。
6. 退出：保存当前草稿为 interrupted。重启把遗留 streaming 草稿和 running agent 标为 interrupted，保留生成计数，不自动重发。强制退出会丢失最后检查点之后未保存的正文。

内部存储分页方法默认返回 agent 1，也可按 agent ID 读取；没有前端历史 RPC。按全局 seq 作为游标但只查询目标 agent；每页按条数与约 1 MiB 编码大小限流，至少返回一条，nextCursor 为 NULL 表示结束。

## 上下文接口与解耦

```text
HistoryStore（持久化 agent 与消息）
    ↓
ContextProvider.build(agent_id)（组织模型上下文）
    ↓
AgentHarness.stream(agent_id)（调用模型）
    ↓
Model.stream(messages)（与存储结构无关）
```

`ContextProvider` 保留 `refresh()` 与 `build(agent_id)` 两个方法。当前 `FullHistoryContextProvider` 加载完整历史，按 agent ID 筛选 completed 消息，加上角色 prompt；不包含 streaming、superseded、failed 或 interrupted 草稿。用户输入本身为 completed，因此回复失败后仍可在后续对话使用。

Service 通过存储方法受理、替换、检查点与结束生成，不编写 SQL；模型适配器只接收模型消息，不知道 agent 表或数据库。UI 只发送 text 和处理正文信号，不读数据库、不知道 agent ID/generation，也不决定开始或替换生成。修改上下文选择策略不需要改 Ark 适配器或人物/气泡。

当前不做摘要、检索或 token 裁剪，超过上游上下文限制明确报错。人设从 `prompts/olivia.md` 读取；修改后重启生效。长期记忆应作为可重建的派生索引，原始历史保留为依据。

## 下一阶段：subagent 控制与结果回流（未实现）

所有直接用户输入仍先保存到 agent 1。主 agent 选择委托或补充，在子 agent 历史中保存输入并引用来源；子历史中的 role=user 表示模型输入，不代表用户直接建立了另一条产品对话。

结果保存在子历史中，携带来源 agent ID、generation 和消息 ID，按 parent_agent_id 回到父 agent。父 agent 的自然转述引用结果消息，不复制整段工具日志。投递状态和已转述/已抑制标记作为内部附属记录，不再引入 task/execution 业务层。

创建与控制按稳定操作记录去重；generation 不作为委托去重键。补充推进目标 agent 的生成版本。取消保存状态与来源并关闭新步骤入口，运行中先标记取消中，核对在途操作后才能标记已取消。取消不回滚外部效果；完成与取消按 agent 串行处理，撤回后抑制普通报喜，迟到结果保留事实。

结果消息与待投递状态需原子提交；父回复持久化完成后才标记已转述。重启不自动恢复已取消工作，也不盲目重放未知外部操作。这些投递、取消和副作用语义在后续 agent 实现中完成。

## 边界

单用户本机历史，不做跨设备同步、数据库加密、历史删除界面或自动归档。不得存储凭证、认证请求头或模型内部推理流。测试使用临时数据库；当前没有向 UI 暴露创建或切换 agent 的入口。
