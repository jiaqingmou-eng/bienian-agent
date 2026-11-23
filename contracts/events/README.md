# 后续流式事件约定

第一天保留协议文档，不提供虚假的 SSE 接口。接入 LangGraph 时统一定义：

```json
{
  "eventId": "evt_unique",
  "runId": "run_unique",
  "type": "approval_required",
  "data": { "approvalId": "approval_uuid", "expenseId": "expense_uuid" }
}
```

事件名计划包括 run_started、retrieval_completed、tool_started、approval_required、message_delta、run_completed、run_failed。

SSE id 对应 eventId。需要定义事件持久化、顺序、重连游标与终态，再实现 BFF 转发。模型内部推理内容不作为用户事件输出。
