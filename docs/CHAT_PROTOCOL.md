# 桌宠持续对话协议

状态：已实现。更新：2026-10-09。前端只有一个输入方法：`chat/send`。存储与内部生成管理见 [历史与记忆](HISTORY_MEMORY.md)，UI 行为见 [总体设计](AGENT_BACKEND.md)。

## 交互边界与原因

用户始终在和同一个 Olivia 说话。前端负责发送文字和显示 Olivia 的文字，不判断一句话是新话题、补充、撤回还是新的委托，也不管理“当前任务”。这些判断属于主 agent。

因此，前端不传 agent ID、generation 或目标回复 ID，不根据是否正在流式输出切换发送方法。后台在接收输入时决定如何处理当前回复。这样无需让前端猜测后端是否已经说完，也不会因为输入与完成事件同时到达而要求用户重新发送。

前端不提供创建会话、停止生成、管理 subagent 或按 agent 查询历史的 RPC。Agent 身份、生成版本、内部取消和历史分页留在 backend。

## 传输与启动

BackendClient 通过 QProcess 启动 `python -u -m backend`。stdin/stdout 传输 UTF-8 JSON-RPC 2.0 JSONL，每行一个对象；stdout 只用于协议，stderr 用于诊断。请求有顶层 id，响应返回相同 id；通知没有 id，不回复。正文换行转义在字符串中。

backend 初始化存储与对话服务后主动通知就绪：

```json
{"jsonrpc":"2.0","method":"backend/ready","params":{}}
```

客户端收到后允许输入；不另发初始化或能力查询请求。params 必须是对象，不支持 batch 和位置参数。

## 唯一输入：chat/send

```json
{"jsonrpc":"2.0","id":1,"method":"chat/send","params":{"text":"陪我聊会儿"}}
{"jsonrpc":"2.0","id":1,"result":{}}
```

参数只有 text，去除首尾空白，长度为 1–10000 字。空文本或额外参数返回参数错误。响应只确认输入已被受理，不描述任何生成或执行状态。

客户端可连续发送，不等上一条回复完成，也不把连续输入分为一组任务。backend 串行受理输入，保持顺序：

- 当前没有生成中的草稿时，保存输入并开始组织回复。
- 当前仍有草稿在生成时，内部结束旧生成、保留用户原话、将旧草稿标记为 superseded，再结合新输入重新组织回复。

上述是当前 backend 的草稿策略，前端不参与选择。新输入可以完全换话题，不必是对前一句的补充。未来主 agent 承担委派判断后仍沿用同一个输入入口。

## 输出：展示消息，不暴露执行状态

```json
{"jsonrpc":"2.0","method":"chat/started","params":{"messageId":"m1"}}
{"jsonrpc":"2.0","method":"chat/delta","params":{"messageId":"m1","text":"好呀，"}}
{"jsonrpc":"2.0","method":"chat/delta","params":{"messageId":"m1","text":"今天过得怎么样？"}}
{"jsonrpc":"2.0","method":"chat/completed","params":{"messageId":"m1","status":"completed","text":"好呀，今天过得怎么样？"}}
```

messageId 只标识正在展示的消息，不用于向 backend 指定控制对象。客户端不用它配对某次输入：一条输入是否立即产生回复，以及之后是否主动开口，都由 backend 决定。

- started 开始展示一条新消息，清空气泡中的旧草稿；显示期间收到新的 started 时切换到新消息。
- delta 追加对应消息的文字。旧消息迟到的 delta/completed 不应用到当前气泡。
- completed.text 为权威全文，校准最终显示，不重复追加。status 表示这条文字流正常结束或失败/中断，不能解释为某项后台工作完成。

backend 当前在受理响应之后发出 started；客户端由 started 建立显示状态，受理响应不携带或设置“活动任务”。有序 JSONL 连接保证通知顺序；generation 检查和过期生成抑制完全在 backend。

消息示例：用户继续说话时，仍发送相同请求：

```json
{"jsonrpc":"2.0","id":2,"method":"chat/send","params":{"text":"其实我今天有点难过"}}
{"jsonrpc":"2.0","id":2,"result":{}}
{"jsonrpc":"2.0","method":"chat/started","params":{"messageId":"m2"}}
```

m2 如何回应由主 agent 决定；前端只按后续正文显示，不把这句话解释为修改某个任务。

## 流式链路

方舟 HTTP SSE 与前端协议分层。backend 收到每个正文片段后立即发出 chat/delta 并 flush，UI 立即更新气泡，不等 completed。普通聊天默认关闭深度思考，不展示或保存模型内部推理流。

关闭气泡只隐藏显示；后台继续接收输入和生成文字，完成时不擅自重新展开。人物素材、尺寸与气泡几何不因本协议改变。

## 错误、超时和退出

- 受理前：JSON-RPC error。-32700 为 JSON 解析失败，-32600 为无效请求，-32601 为未知方法，-32602 为参数错误，-32603 为后台处理失败。正常聊天不返回“正忙，请改用另一方法”或“回复已结束，请重发”。
- 受理后：chat/completed 包含已经生成的正文、failed 状态与 `error: {code, message}`。不透传 SDK 原始异常、认证信息或内部执行日志。
- 启动就绪与发送受理超时均为 10 秒；SDK 网络超时 60 秒；内部单次生成总时限 300 秒；输入帧最大约 1 MiB，正文最多 200000 字符。
- 断开后可手动重连，不自动重发未确认输入，避免重复。受理失败的文本恢复到输入框。
- 关闭进程的 stdin 触发后台内部收尾，保存未完正文；不通过前端的取消 RPC。重启恢复历史，主对话继续。

## 与后续 agent 设计的关系

Subagent 只与父 agent 和 backend 运行时交互，结果不直接显示到气泡。主 agent 将结果结合持续对话生成自己的消息，再走相同的 started/delta/completed 通知。

客户端已经能在就绪后接收不依赖 send 响应的 started；产生主动消息所需的后台事件唤醒、时机判断和持久化投递尚未实现。后续接入它们不应重新让前端承担 agent 或生成调度。
