# paper-fetch 中文文档

`paper-fetch` 是一个面向单篇论文的 PDF 获取工具：输入 DOI、PMID、PMCID、准确标题、完整引文或 Zotero 条目 key，按预定来源顺序获取有效 PDF，并可自动上传到 Zotero。

- 英文文档：[README.md](README.md)
- GitHub：[CaseyTso/paper-fetch](https://github.com/CaseyTso/paper-fetch)

## 适用场景

适用于已经确定具体论文、希望下载 PDF 或补齐 Zotero 附件的场景。不用于关键词检索、广泛文献发现或批量搜索。

## 名称说明

| 层级 | 名称 | 说明 |
|---|---|---|
| 项目、仓库、发行包、CLI、配置目录、环境变量前缀 | `paper-fetch` | 对外使用的产品名称 |
| Python import 包 | `paper_fetch` | Python 标识符不能使用连字符 |
| 配置文件 | `~/.paper-fetch/config.json` | 本地个人配置，不提交到仓库 |
| 环境变量 | `PAPER_FETCH_*` | 覆盖 JSON 配置 |

## 安装

### 从 GitHub 安装用户级 CLI

```bash
uv tool install "git+https://github.com/CaseyTso/paper-fetch.git"
paper-fetch --help
```

### 从本地仓库开发安装

```bash
git clone https://github.com/CaseyTso/paper-fetch.git
cd paper-fetch
uv sync --extra dev
uv run paper-fetch --help
```

### 本地 editable 安装

适合持续开发。源码修改后，CLI 会直接使用工作区代码：

```bash
uv tool install --editable --force .
paper-fetch --help
```

## 配置

创建 `~/.paper-fetch/config.json`，建议设置权限为 `0600`：

```json
{
  "output_dir": "/Users/you/Downloads/papers",
  "unpaywall_email": "you@example.com",
  "institution_socks5": "socks5h://127.0.0.1:<PORT>",
  "institution_tls_verify": true,
  "clash_proxy": "http://127.0.0.1:<PORT>",
  "zotero_library_id": "YOUR_LIBRARY_ID",
  "zotero_library_type": "user",
  "zotero_inbox_collection_key": "YOUR_00_INBOX_COLLECTION_KEY",
  "zotero_api_key": "YOUR_ZOTERO_API_KEY",
  "ablesci_url": "https://www.ablesci.com"
}
```

所有字段均可选。请填写你自己的机构认证、代理和 Zotero 信息；本项目不会发布个人配置。环境变量 `PAPER_FETCH_*` 的优先级高于 JSON 配置，例如：

```bash
export PAPER_FETCH_ZOTERO_API_KEY="YOUR_ZOTERO_API_KEY"
```

### 机构访问

- `institution_socks5` 支持 SOCKS5 URL，也支持 aTrust 暴露的 HTTP 代理 URL。
- `institution_tls_verify` 默认为 `true`。
- 只有在机构客户端使用本地 MITM 证书、且 Python 不信任该证书时，才将其设为 `false`；该设置只影响机构访问请求。

## 使用方法

命令始终建议使用 `--json`，便于程序或 Agent 解析结果。

### 按 DOI

```bash
paper-fetch fetch '10.1371/journal.pmed.0020124' --json
```

### 按 PMID

```bash
paper-fetch fetch '16060722' --json
```

### 按 PMCID

```bash
paper-fetch fetch 'PMC1182327' --json
```

### 按准确标题

```bash
paper-fetch fetch 'Why most published research findings are false' --json
```

### 按 Zotero 条目 key

```bash
paper-fetch fetch 'zotero:ABCD1234' --json
```

### 只保存本地，不上传 Zotero

```bash
paper-fetch fetch \
  '10.1371/journal.pmed.0020124' \
  --json \
  --no-zotero \
  --output /tmp/paper-fetch-out
```

可用选项：

- `--output <目录>`：覆盖默认下载目录。
- `--no-zotero`：只保存 PDF，不执行 Zotero 操作。
- `--force`：即使已经缓存或附加到 Zotero，也重新获取。

## 来源顺序

工具在获取到第一个有效多页 PDF 后停止：

1. **开放获取**：PMC → Europe PMC → PubMed 全文链接 → Unpaywall
2. **机构访问**：EasyConnect/aTrust SOCKS5 或 HTTP 代理
3. **Sci-Hub**：通过 Clash HTTP 代理
4. **科研通 ableSci**：Chrome cookies HTTP API，必要时回退到 OpenCLI Browser Bridge

JSON 结果中的 `attempts` 会记录各来源的尝试顺序、状态和耗时。

## Zotero 集成

默认情况下，成功获取 PDF 后会写入 Zotero：

1. 如果 DOI 对应的 Zotero 条目已存在，将 PDF 附加到该条目。
2. 如果不存在，则创建期刊文章条目，并填写作者、标题、期刊、DOI、PMID 等元数据。
3. PDF 通过 Zotero 官方文件上传协议上传，并镜像到本地 Zotero storage 目录。

如不希望修改 Zotero，请使用 `--no-zotero`。

以下状态通常需要人工操作：

- `authentication_required`：先在浏览器中登录科研通，再重新运行命令。
- `challenge_required`：在浏览器中完成 Sci-Hub CAPTCHA/ALTCHA，再重新运行命令。
- `pending` 或 `poll_timeout`：科研通请求仍在处理中，不代表论文不存在；记录 request ID，稍后重试。

## Hermes Skill

仓库根目录中的 `SKILL.md` 是 Skill 的唯一源码，`references/` 包含配套协议与排错文档。若在 Hermes 中使用，建议让 Hermes 的 Skill 路径符号链接到本仓库根目录，使 Skill 文档与 CLI 共享同一份可编辑源码，避免版本漂移。

个人认证信息应保存在本机配置、浏览器登录状态或机构客户端中，不要写入 Git 仓库，也不要提交 `.env`、cookies、API key、下载的 PDF 或本地进度文件。

## 测试

运行非实时集成测试：

```bash
uv run pytest
```

运行需要网络、凭据或浏览器交互的实时测试：

```bash
uv run pytest -m live
```

实时测试默认不会被普通测试命令执行；请只在已经准备好相应认证和外部服务时运行。

## 合规提示

本工具会通过多个来源获取论文。请遵守出版商条款、所在机构的访问政策以及适用的版权法律，只配置和使用你有权访问的来源。
