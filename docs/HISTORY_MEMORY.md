# 聊天历史与记忆接口

状态：SQLite 历史与全量上下文已实现；检索记忆、摘要与压缩尚未实现。更新：2026-10-08。

## 数据归属和位置

历史唯一写入方是 backend，UI 通过 RPC 读取。数据库不在 Git 仓库里，默认路径：

- macOS：`~/Library/Application Support/Olivia Lin Fan Pet/chat.sqlite3`
- Linux：`${XDG_DATA_HOME:-~/.local/share}/olivia-desktop-pet/chat.sqlite3`

`OLIVIA_DB_PATH` 可以覆盖路径。SQLite 启用 WAL、外键和事务；旁边的 `.lock` 文件通过系统文件锁保证同一数据库只有一个 backend 使用。第二个实例失败退出，不并发生成互相污染上下文。锁随进程退出释放，锁文件保留不影响重启。

`PRAGMA user_version=1` 标记 schema。未知版本拒绝打开，后续修改表结构需要显式迁移。

## Schema

不引入 thread 表，也不按产品“会话”切割历史。

### chat_runs：一次输入及其连续补充的执行状态

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | TEXT PRIMARY KEY | UUID hex，对外为 `chatId` |
| `status` | TEXT NOT NULL | `running / completed / failed / interrupted` |
| `generation` | INTEGER NOT NULL | 当前生成版本，从 1 起，每次 steer 加 1 |
| `model` | TEXT NOT NULL | 调用的 endpoint / 模型标识 |
| `created_at`, `updated_at` | TEXT NOT NULL | UTC ISO 8601，毫秒精度 |
| `finished_at` | TEXT NULL | 最终结束时间 |
| `error_code` | TEXT NULL | 可分类错误码，不保存原始异常或认证信息 |
| `usage_json` | TEXT NULL | 上游实际返回的用量信息；无数据时为 NULL，不推算 |

### messages：原始消息与生成草稿

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `seq` | INTEGER PRIMARY KEY AUTOINCREMENT | 稳定全局排序和分页游标 |
| `id` | TEXT UNIQUE NOT NULL | 每条消息的 UUID hex |
| `chat_id` | TEXT NOT NULL | 外键关联 `chat_runs.id` |
| `generation` | INTEGER NOT NULL | 用户输入在哪一版加入 / assistant 所属生成版本 |
| `role` | TEXT NOT NULL | `user / assistant` |
| `content` | TEXT NOT NULL | 原始用户输入或累积 assistant 文本 |
| `status` | TEXT NOT NULL | `completed / streaming / superseded / failed / interrupted` |
| `created_at`, `updated_at` | TEXT NOT NULL | UTC ISO 8601 |

索引：`messages(chat_id, seq)`。System prompt 是可编辑配置，不作为用户聊天消息写入数据库。输入补充保留为独立用户消息，不直接覆盖原文。

## 写入与恢复

1. `chat/send`：在同一个事务创建 running 执行、completed 用户输入和 streaming assistant 草稿，再确认受理。
2. 流式生成：正文在内存累积，约每 250 ms 更新一次草稿检查点；不为每个 token 插入消息。
3. steer：取消旧任务，事务内保存旧草稿全文并标记 superseded、更新版本、追加用户补充、新建 assistant 草稿。
4. 正常完成：事务内保存全文，将消息和执行标记 completed；提交后通知 UI。
5. 模型失败或用户停止：保存部分文本，标记 failed / interrupted，保留全部用户输入。
6. 正常退出保存中断状态；崩溃后重启，把残留 streaming 消息和 running 执行改为 interrupted，不自动重试。强制退出会丢失最后一次检查点之后尚未持久化的片段。

示例：用户说 A，生成了一半 B，又补充 C，重新生成 D：

| role | content | generation | 最终状态 |
| --- | --- | --- | --- |
| user | A | 1 | completed |
| assistant | B（旧草稿） | 1 | superseded |
| user | C | 2 | completed |
| assistant | D | 2 | completed |

模型后续上下文包含 A、C、D，原始历史查询仍能查到 B。一次失败的执行整体不会当作成功对话放进后续上下文；用户可把本次全部输入重新填入并发送。

## 上下文接口

```text
HistoryStore（原始持久化）
    ↓
ContextProvider（选择并组织模型上下文）
    ↓
AgentHarness（流式调用模型）
```

`backend/context.py` 定义 `ContextProvider` 接口：

- `refresh()`：在持久化状态改变后刷新上下文材料。
- `build(chat_id)`：构造本次模型请求的消息列表。

首版 `FullHistoryContextProvider` 在启动时加载全部原始消息，执行开始、steer 或结束后刷新内存快照。`build()` 返回当前人设提示词 + 全部成功历史交互 + 本次所有用户输入，按全局序号排序，排除失败/中断执行和 superseded 草稿。

人设从 `prompts/olivia.md` 读取；修改后重启生效。当前没有摘要、检索、token 裁剪，也不会悄悄丢弃更早的成功对话。若超出模型上下文上限，调用按错误结束，保留原始历史。

未来可替换 ContextProvider 实现，让 `build()` 根据长期记忆、近期消息和检索结果组织上下文。原始 SQLite 历史保持事实来源；memory 索引/摘要作为可重建的派生数据，UI 和聊天协议不因此改变。具体记忆 schema 和检索策略不属于首版。

## 边界

当前是单用户本机历史，不做同步、数据库加密、历史删除界面或自动归档。测试使用临时数据库，真实 API 冒烟也只发送合成测试对话。不得把 Key、认证请求头或模型内部推理流保存成聊天消息。
