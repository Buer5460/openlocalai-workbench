# 扩展协议

核心数据协议保持简单 JSON，外部模型、OCR、检索器或业务工具均可作为独立进程接入，避免平台绑定单一厂商。

## 工具最小描述

```json
{
  "name": "openlocalai_search_evidence",
  "title": "检索本地证据",
  "description": "在当前授权知识空间中检索与问题相关的原文片段",
  "input_schema": {
    "type": "object",
    "properties": {
      "question": {"type": "string", "minLength": 1, "maxLength": 2000},
      "limit": {"type": "integer", "minimum": 1, "maximum": 20}
    },
    "required": ["question"],
    "additionalProperties": false
  },
  "annotations": {
    "readOnlyHint": true,
    "destructiveHint": false,
    "idempotentHint": true,
    "openWorldHint": false
  }
}
```

扩展返回 `items`、`count`、`has_more`、`next_offset`；单次输出不超过 25,000 字符，超限时返回 `truncated: true`。错误必须给出可操作原因，不得把密钥、原始敏感数据或内部堆栈暴露给调用方。

## MCP 映射

后续可选 MCP 服务器命名为 `openlocalai_mcp`，工具使用 `openlocalai_` 前缀。只读检索可直接调用；导入、发布、审批和外部写入属于有副作用操作，宿主必须展示预览并取得用户明确确认。MCP 适配器是可选包，核心离线流程不依赖 MCP SDK。

当前 MCP Python SDK 稳定线为 v2，适配器实现时应固定兼容版本并依据官方迁移文档测试，避免沿用旧版 FastMCP API。
