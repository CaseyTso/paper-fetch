# Zotero 本地写入路径可行性研究报告

**日期**: 2026-07-27
**结论**: 三路均不支持无云端的完整本地写入；最短替代方案是保持现有 Web API 路径并修复同步间隙。

---

## 1. 内置 MCP（端口 23120）

- **端点**: `POST http://127.0.0.1:23120/mcp` (JSON-RPC 2.0 over Streamable HTTP)
- **工具数**: 20
- **写工具**: `create_collection`、`update_collection`、`delete_collection`、`add_items_to_collection`、`remove_items_from_collection`
- **缺失能力**: 无 `create_item`、`attach_file`、`import_pdf`
- **结论**: ❌ 只能管理集合，不能创建条目或附加 PDF

```bash
# 实测命令
curl -s -X POST http://127.0.0.1:23120/mcp \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
# 返回 20 个工具，无 create_item / attach_file
```

## 2. 第三方 zotero-mcp（v0.5.0 → v0.6.2）

- **写工具**: `zotero_add_by_doi`、`zotero_add_by_url`、`zotero_add_from_file`、`zotero_batch_update_tags`、`zotero_create_collection`、`zotero_manage_collections`
- **本地限制**: 所有写工具调用 `_get_write_client()` → 在 `local-only` 模式下抛出 `ValueError("Cannot perform write operations in local-only mode. Add ZOTERO_API_KEY and ZOTERO_LIBRARY_ID to enable hybrid mode.")`
- **v0.6.2 无变化**: 升级不解除此限制；该限制是架构性的——Zotero 写操作必须通过 Web API 或直接 SQLite
- **结论**: ❌ 纯本地模式不能写；hybrid 模式需要 API key 且写入云端

```bash
# 实测命令
env -u PYTHONPATH -u ZOTERO_API_KEY ZOTERO_LOCAL=true \
  zotero-mcp serve --transport sse --port 8765
# 调用 zotero_add_by_doi → ValueError: Cannot perform write operations in local-only mode
```

## 3. zotero-cli

- **写命令**: `add doi`、`add url`、`add file`
- **本地限制**: 同上，`ZOTERO_LOCAL=true` 且无 API key 时返回 `"Cannot perform write operations in local-only mode."`
- **结论**: ❌ 同 zotero-mcp

```bash
env -u PYTHONPATH -u ZOTERO_API_KEY ZOTERO_LOCAL=true \
  zotero-cli add doi "10.1038/s41586-025-99999"
# → "Cannot perform write operations in local-only mode."
```

## 4. 端口 23119 Connector

- **协议**: WebSocket（浏览器扩展专用）
- **HTTP 探测**: `GET /`、`GET /connector`、`GET /connector/getStatus` 均返回 `"No endpoint found"`
- **结论**: ❌ 不对非浏览器客户端开放

---

## 最短替代方案

**保持现有 Web API 路径**（paper-fetch 已实现），解决"条目已创建但 Zotero Desktop 不可见"的同步间隙：

1. **当前问题**: paper-fetch 通过 Web API 创建条目→条目存在于 zotero.org→Zotero Desktop 需手动同步才能看到
2. **原因**: Zotero Desktop 不会自动检测外部 API 写入；文件同步依赖 WebDAV（用户已到期）
3. **建议方案（按优先级）**:
   - **A. 触发本地同步**: 写入后通过 Zotero 内置 MCP 无对应能力、AppleScript/Zotero URL scheme 无 sync 端点 → 不可行
   - **B. API 写入 + 通知用户手动同步**: 在 CLI 输出中提示"请在 Zotero Desktop 中按 Cmd+Shift+S 同步"
   - **C. 保持现行流程**: 用户只需偶尔手动同步一次，条目和 PDF 即可拉取到本地
