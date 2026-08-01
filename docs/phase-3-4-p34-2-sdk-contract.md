# P34.2 Python/TypeScript SDK 与公开契约

> 状态：实现基线。该文档不代表 P34.2 总 Gate 或生产发布已完成。

## 1. 公开能力面

SDK 只暴露以下四个只读动作，全部接受逻辑 UUID，不接受 tenant、workspace、
schema、table、SQL、path、MinIO key 或 provider handle：

| Action | Method and path |
|---|---|
| `data.schema.read` | `POST /gateway/v1/data/schema/read` |
| `data.rows.read` | `POST /gateway/v1/data/rows/read` |
| `rag.search` | `POST /gateway/v1/rag/search` |
| `rag.citation.read` | `POST /gateway/v1/rag/citations/read` |

四条路径固定为 `POST`，因为逻辑列集合、结构化 filter AST、query 和 citation ID
列表不应进入 URL、代理访问日志或浏览器历史。Gateway 是独立 workload app，不挂载
`/api/v1`，不接受浏览器 CORS 和用户 JWT。

## 2. 身份与凭据

- `Authorization` 固定使用 `Capability <short-lived-token>`，明确拒绝 `Bearer`。
- SDK 每次请求前调用 `WorkloadCredentialProvider`，不缓存、序列化或记录 token。
- SDK 发送非秘密的 runtime workload identity，Gateway 只把它作为待校验输入；权限
  事实来自签名 token、mTLS/受信代理上下文和在线 grant ledger 的联合验证。
- 证书、私钥与验证后的证书 thumbprint 由 runtime 网络/mTLS 层处理。SDK 不允许调用方
  自报受信 thumbprint。
- PAT 仅保留为未来“人类或外部程序访问控制面”的独立设计，不得交给 Workspace，
  不得用于 `/gateway/v1`，也不得替代 workload capability。

## 3. DTO 与错误

请求和响应模型均 `extra=forbid`。rows cursor 是 opaque continuation token；客户端只
原样回传，不解析、不修改，也不从中推导 tenant、workspace 或物理位置。RAG search
只返回 citation ID 和有界 snippet，正文由单独的 citation read 动作按 ID 获取。

错误 envelope 固定为：

```json
{"error":{"code":"invalid_capability","message":"Capability authentication required"}}
```

SDK 从 `X-Request-Id` 获取关联 ID，并放入异常对象；错误 body 不允许携带 token、claim、
grant、JTI、locator、内部异常或 debug details。签名失败、过期、撤销、版本不一致与
workload binding 不一致统一为 `401 invalid_capability`，避免形成验证 oracle。

## 4. Transport 安全边界

- 默认只允许 HTTPS；明文 HTTP 仅能由调用方显式开启且 host 必须是 loopback。
- base URL 必须是纯 origin，不得嵌入凭据、path、query 或 fragment。
- Python transport 禁止自动重定向；TypeScript fetch 使用 `redirect: "error"`，防止
  Capability 和 workload header 被带到其他 origin 或降级通道。
- Python/TypeScript transport 都流式限制响应字节数，成功和错误 body 同样受限。
- SDK 严格解析 Gateway DTO，不执行字符串到 boolean/integer 等宽松转换。
- SDK 不写日志、不写 local storage/cookie、请求使用 `credentials: "omit"`。

## 5. Breaking-change Gate

`sdk/contracts/p34-2-openapi.snapshot.json` 冻结：

- 四条路径和唯一 method；
- operation ID；
- request/response component；
- 公共模型字段集合。

契约测试同时扫描物理 locator、租户 scope、token/grant/JTI 等禁用字段，验证错误状态
统一使用安全 envelope 和 `X-Request-Id`，并锁定 cursor、行数、bytes、timeout、top-k
上限。任何有意 breaking change 必须升级 contract version、同步两种 SDK，并经过独立
安全评审；不得只刷新 snapshot 来绕过失败。
