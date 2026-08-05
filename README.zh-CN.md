# paper-fetch

面向 Agent 的单篇论文 PDF 获取 Skill 与 CLI，并支持 Zotero 附件管理。

[English documentation](README.md)

`paper-fetch` 首要服务对象是 AI Agent：Agent 提供一个 DOI、PMID、PMCID、准确标题、完整引文或 Zotero 条目 key，调用一次命令，即可按固定来源顺序获取论文，并通过 JSON 结果继续处理下载文件或 Zotero 附件。

- GitHub：[CaseyTso/paper-fetch](https://github.com/CaseyTso/paper-fetch)
- Hermes Skill 源文件：[`SKILL.md`](SKILL.md)
- Skill 参考文档：[`references/`](references/)

## 它解决什么问题

- 解析单篇论文的 DOI、PMID、PMCID、准确标题或 Zotero key。
- 按固定顺序尝试配置好的获取来源。
- 验证结果确实是有效的多页 PDF。
- 输出包含来源、文件路径、尝试记录、状态和错误信息的 JSON。
- 可选地创建或更新 Zotero 条目，并上传 PDF 附件。

它不是关键词检索工具，也不是批量文献发现工具。多个论文应由 Agent 逐篇调用单篇命令。

## 安装 CLI

内置的跨平台安装器会自动检测环境并选择两套安装路径之一：

```bash
sh scripts/minis-install.sh    # 在仓库克隆 / skill 目录中运行
```

- **Minis（iSH/iOS）**：检测到 `/var/minis` 且 PATH 中有 `minis-browser-use`
  时启用。通过 Alpine 系统包 + `--system-site-packages` venv 安装，并输出
  Minis 专属的 ableSci 配置提示（见
  [Minis（iOS / iSH）适配](#minisios--ish适配)）。
- **macOS / glibc Linux**：`uv tool install`（无 `uv` 时用 venv + pip），
  ableSci 走经典 Chrome cookies 路径。

手动安装的等价方式：

### 从 GitHub 安装用户级 CLI

```bash
uv tool install "git+https://github.com/CaseyTso/paper-fetch.git"
paper-fetch --help
```

### 从仓库克隆并安装开发环境

```bash
git clone https://github.com/CaseyTso/paper-fetch.git
cd paper-fetch
uv sync --extra dev
uv run paper-fetch --help
```

### 本地 editable 安装

开发 Skill 和 CLI 时使用。修改 `src/` 后，CLI 会直接使用工作区代码：

```bash
uv tool install --editable --force .
paper-fetch --help
```

## Minis（iOS / iSH）适配

paper-fetch 将 **Minis**——跨平台 AI 智能体应用
（[OpenMinis/OpenMinis](https://github.com/OpenMinis/OpenMinis)，GPL-3.0，
OpenMinis 作者开发）——视为一等公民环境。安装器和 CLI 会自动检测
（`/var/minis` 存在且 PATH 中有 `minis-browser-use`）并适配：

### Minis 环境的行为差异

| 方面 | 桌面（macOS/Linux） | Minis（iSH/iOS） |
|---|---|---|
| 安装 | `uv tool install` | `sh scripts/minis-install.sh` → Alpine 系统包 + venv |
| ableSci 传输 | Chrome cookies（`browser-cookie3`）→ OpenCLI 回退 | **Minis WebView 驱动**（`minis-browser-use`） |
| ableSci 登录 | 在 Google Chrome 登录一次 | 在内置浏览器登录一次（会话持久） |
| 开放获取 / Unpaywall | 正常 | 正常 |
| Sci-Hub | 经 Clash 代理 | 经 Clash 代理（设备上需运行 Clash） |
| 机构代理 | EasyConnect/aTrust SOCKS5 | 相同（若设备上运行了 VPN 客户端） |
| Zotero | 完整上传 | 配置好 Zotero Web API 凭据即可上传 |

### 为什么 ableSci 需要浏览器驱动？

在 iSH 中，桌面的 ableSci 路径全都无法工作：

- `browser-cookie3` 需要 DBUS/Secret Service 以及访问 iOS Chrome 的 cookie
  库——iSH 沙盒里两者都不存在。
- OpenCLI 驱动桌面 Chrome——iOS 上没有 Chrome。
- 纯 HTTP 客户端会被 ableSci 的阿里云 WAF（`security_session_verify` +
  TLS 指纹校验）拦截并重定向到 `/site/login`，即使 cookies 有效也不行；
  且网站提供的 PDF 是加密的，必须由网站自己的 JavaScript 解密。

因此 paper-fetch 通过 `minis-browser-use` CLI 驱动**内置 WebView**：先检查
登录态（通过受保护页面判断，因为移动端 header 折叠了退出/用户名文本），
提交求助表单，轮询下载链接，打开下载页，等待原生下载落到 workspace，并
采纳文件。若同一 DOI 已有可下载的既有求助，会直接复用，避免重复花费积分。

### 配置

`ablesci_driver`（默认 `auto`）选择传输方式：`auto` 在 Minis 内优先
WebView 驱动，在桌面上优先 Chrome-cookie HTTP API；`http`、`browser`、
`opencli` 可强制指定某一路径。

```bash
paper-fetch doctor --json    # 报告当前可用的 ableSci 路径
```

### Minis 环境已知限制

- ableSci 下载经浏览器原生下载落盘；文件要等下载页 JS 解密完成后才会出现
  在 `/var/minis/workspace/`（文件名带 `科研通-ablesci.com` 后缀）。
- WebView 驱动是顺序执行的，一次 ableSci 求助约需 1–2 分钟
  （提交 → 轮询 → 下载 → 采纳）。
- Sci-Hub 仍需设备上可达的 Clash 代理（配置项 `clash_proxy`）。

## 安装到 Hermes

仓库根目录包含完整 Skill：`SKILL.md` 以及配套的 `references/` 目录。可以选择以下方式。

### 推荐：克隆仓库并建立符号链接

适合 Skill 持续开发。仓库是唯一可编辑源，Hermes 读取的就是你正在修改和测试的文件：

```bash
mkdir -p "${HERMES_HOME:-$HOME/.hermes}/skills/research"
git clone https://github.com/CaseyTso/paper-fetch.git "$HOME/paper-fetch"
ln -sfn "$HOME/paper-fetch" "${HERMES_HOME:-$HOME/.hermes}/skills/research/paper-fetch"
hermes skills list | grep paper-fetch
```

如果本地已经有仓库：

```bash
cd "$HOME/paper-fetch"
git pull --ff-only origin main
```

验证链接：

```bash
python3 - <<'PY'
from pathlib import Path
import os
repo = Path.home() / "paper-fetch"
installed = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "skills/research/paper-fetch"
print("实际路径:", installed.resolve())
print("是否指向仓库:", installed.resolve() == repo.resolve())
PY
```

如果使用其他本地克隆路径，将 `$HOME/paper-fetch` 替换为该路径。不要把包含个人认证信息的 Skill 副本提交到公开仓库；个人配置应放在 Git 之外。

### 从公开 SKILL.md 安装独立副本

Hermes 支持从直接的 `SKILL.md` URL 安装 Skill：

```bash
hermes skills install \
  "https://raw.githubusercontent.com/CaseyTso/paper-fetch/main/SKILL.md" \
  --category research \
  --name paper-fetch \
  --yes
```

这种方式适合只安装、无需参与开发的用户。开发者应优先使用“克隆仓库并建立符号链接”，这样 Skill 与参考文档始终和仓库同步。

安装后验证：

```bash
hermes skills list | grep paper-fetch
hermes skills inspect paper-fetch
```

### Hermes Profile

Hermes 当前使用的 Profile 决定 Skill 目录。如果需要为其他 Profile 安装，应先设置对应的 `HERMES_HOME`：

```bash
HERMES_HOME="$HOME/.hermes/profiles/<profile>" \
  hermes skills list
```

请勿误修改其他用户或其他 Profile 的 Skill 目录。

## 安装到 Claude Code

同一仓库根目录也是 Claude Code 插件：根目录 `SKILL.md` 与 `references/`，清单在 [`.claude-plugin/`](.claude-plugin/)。Skill 仍依赖 `paper-fetch` CLI 与 `~/.paper-fetch/config.json`（见上文）。Hermes 与 Claude Code 可共用同一份 clone。

### 推荐：从 GitHub 添加 marketplace

```bash
claude plugin marketplace add CaseyTso/paper-fetch
claude plugin install paper-fetch@paper-fetch
```

执行 `/reload-plugins` 或新开 Claude Code 会话。用 `/paper-fetch` 调用（Claude Code 2.1.220 实测），或用自然语言要求下载论文 PDF 到 Zotero。

`plugin.json` 含 `"skills": ["./"]`，以便当前 Claude Code 发现根目录 `SKILL.md`；2.1.142+ 也可在无该字段时自动暴露根级 `SKILL.md`。

验证：

```bash
claude plugin list
```

应看到已启用的 `paper-fetch@paper-fetch`。

### 开发：本地 marketplace 或符号链接

本地 clone（路径自定；示例用 `$HOME/paper-fetch`）：

```bash
claude plugin marketplace add "$HOME/paper-fetch"
claude plugin install paper-fetch@paper-fetch -s user
```

或不走 marketplace，直接链到个人 skill 目录：

```bash
mkdir -p "$HOME/.claude/skills"
ln -sfn "$HOME/paper-fetch" "$HOME/.claude/skills/paper-fetch"
```

无论哪种方式都需要 CLI：

```bash
uv tool install "git+https://github.com/CaseyTso/paper-fetch.git"
```

## 命名说明

| 层级 | 名称 | 说明 |
|---|---|---|
| 发行包、CLI、仓库、配置目录、环境变量前缀 | `paper-fetch` | 对外产品名称 |
| Python import 包 | `paper_fetch` | Python 标识符不能使用连字符 |
| 个人配置 | `~/.paper-fetch/config.json` | 仅用于本地运行，不提交 |
| 环境变量 | `PAPER_FETCH_*` | 覆盖 JSON 配置 |

## 配置

创建 `~/.paper-fetch/config.json`，并设置权限为 `0600`：

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

所有字段均可选。只配置 `output_dir` 和 `unpaywall_email` 时，开放获取下载可以正常工作；**未配置的后备来源会保持关闭**：

| 来源 | 启用所需的字段 | 未配置时的行为 |
|---|---|---|
| 机构访问 | `institution_socks5` | 跳过该来源 |
| Sci-Hub | `clash_proxy` | 跳过该来源 |
| 科研通 | `ablesci_url` + Chrome 登录 | 跳过该来源 |
| Zotero | `zotero_library_id`、`zotero_library_type`、`zotero_inbox_collection_key`、`zotero_api_key` | PDF 仅保存到本地（等同 `--no-zotero`） |

环境变量（例如 `PAPER_FETCH_ZOTERO_API_KEY`）优先于 JSON 配置。不要提交 API key、cookies、本地路径或下载的 PDF。

### 首次配置自检：`paper-fetch doctor`

安装 CLI 后先运行只读自检：

```bash
paper-fetch doctor --json
```

它不会写入任何内容，只检查：配置文件（存在、可解析、权限）、机构代理（已配置、URL 合法、端口可达；即使未配置也会提示检测到运行中的 EasyConnect/aTrust 客户端）、Clash 代理（同样的检查）、科研通会话（URL 已设置、Chrome cookies 可读、OpenCLI 后备可用）以及 Zotero 字段。

- JSON `overall` 为 `ok` 表示全部就绪；`needs_configuration` 表示存在未配置的可选项目（退出码 0，开放获取下载仍可用）；`error` 表示配置文件无法读取（退出码 5）。
- 每项检查都带 `action`——人类或 Agent 的下一步动作。
- 报告不会输出凭据、cookie 值或 API key。

### 机构访问（EasyConnect / aTrust）

1. **先登录机构 VPN 客户端**（EasyConnect 或 aTrust），保持连接。
2. **找到本地代理端口**：

   ```bash
   lsof -nP -iTCP -sTCP:LISTEN
   ```

   找到 VPN 客户端监听的端口。
3. **区分 HTTP 与 SOCKS5**。aTrust 通常暴露的是 HTTP 代理，尽管字段名是 `institution_socks5`。探测候选端口：若 `curl -x http://127.0.0.1:<PORT> -sS -o /dev/null -w '%{http_code}\n' https://api.crossref.org` 返回 200，则配置 `http://...`；否则使用 `socks5h://...`。
4. **配置**：

   ```json
   {
     "institution_socks5": "http://127.0.0.1:<PORT>",
     "institution_tls_verify": true
   }
   ```

   `institution_tls_verify` 默认为 `true`；仅当 VPN 客户端使用本地 MITM 证书而 Python 不信任时才设为 `false`。该设置只影响机构来源。VPN 重启后端口可能变化——重新运行 `paper-fetch doctor` 确认。
5. 用 `paper-fetch doctor --json` 验证（`institution` 一行应为 `ok`）。

完整探测流程与排障：[`references/institution-access.md`](references/institution-access.md)。

### 通过 Clash 使用 Sci-Hub

1. 启动 Clash 并连接节点。
2. 从客户端设置中读取 HTTP 或 Mixed 端口（ClashX 通常是 7890）——不要猜。
3. 验证：`curl -x http://127.0.0.1:<PORT> -sS -o /dev/null -w '%{http_code}\n' https://api.ip.sb/ip` 应返回 200。
4. 在配置文件中写入 `"clash_proxy": "http://127.0.0.1:<PORT>"`，再用 `paper-fetch doctor --json` 确认。
5. Sci-Hub 验证码会自动求解：CLI 检测到 ALTCHA 工作量证明页后自动计算并带 cookie 重试。仅当自动求解失败时 fetch 才返回 `challenge_required`（此时可在浏览器打开该网址人工完成后再重跑）。

设置指南：[`references/scihub-clash-setup.md`](references/scihub-clash-setup.md)。

### 科研通 ableSci

1. 在对应环境的浏览器中打开 <https://www.ablesci.com> 并**登录一次**：
   - **Minis 环境** → 在内置浏览器中登录（会话跨运行持久）。paper-fetch
     通过 `minis-browser-use` 驱动它，**不会保存或索要你的科研通密码**。
   - **桌面环境** → 在 Google Chrome 中登录；paper-fetch 从 Chrome cookies
     读取会话。
2. 配置 `"ablesci_url": "https://www.ablesci.com"`。
3. 传输方式（`ablesci_driver`，默认 `auto`）：
   - Minis 环境 → Minis WebView 驱动（首选）；Chrome cookies / OpenCLI 在
     iSH 沙盒中不可用（无 DBUS、无 iOS Chrome 库、阿里云 WAF 拦截纯 HTTP）。
   - 桌面环境 → Chrome-cookie HTTP API（首选），安装了 `opencli` 时回退到
     OpenCLI Browser Bridge。`paper-fetch doctor --json` 会报告当前可用路径。
4. 若 fetch 返回 `authentication_required`，在对应环境的浏览器中重新登录
   科研通（Minis 用内置浏览器，桌面用 Chrome），再运行同一条命令。

用户指南：[`references/ablesci-login.md`](references/ablesci-login.md)。

## Agent 使用方法

Agent 应该每次只处理一篇论文，并始终请求 JSON：

```bash
paper-fetch fetch '<论文标识>' --json
```

支持的输入包括 DOI、PMID、PMCID、准确标题、完整引文和 `zotero:<ITEM_KEY>`。

关键 JSON 字段：

| 字段 | 含义 |
|---|---|
| `success` | 是否获取到有效的多页 PDF |
| `source` | 成功获取文件的来源 |
| `pdf_path` | PDF 的绝对路径 |
| `zotero_item_key` | 成功附加时的 Zotero 父条目 key |
| `attempts` | 各来源的尝试顺序、状态和耗时 |
| `error` | 失败时的人类可读摘要 |

常用选项：

- `--output <目录>`：覆盖输出目录。
- `--no-zotero`：只保存到本地，不修改 Zotero。
- `--force`：即使已有缓存或附件，也重新获取。

首次下载前（或某个来源行为异常时）先运行 `paper-fetch doctor --json`，并按报告中的检查项行动（见[配置](#配置)）。

示例：

```bash
paper-fetch fetch \
  '10.1371/journal.pmed.0020124' \
  --json \
  --no-zotero \
  --output /tmp/paper-fetch-out
```

## 来源顺序

获取到第一个有效多页 PDF 后停止：

1. **开放获取**：PMC → Europe PMC → PubMed 全文链接 → Unpaywall
2. **机构访问**：EasyConnect/aTrust SOCKS5 或 HTTP 代理
3. **Sci-Hub**：Clash HTTP 代理
4. **科研通 ableSci**：Minis WebView 驱动（Minis 环境）、Chrome cookies HTTP API（桌面首选）、OpenCLI Browser Bridge 回退

科研通请求可能是异步的。`pending` 或 `poll_timeout` 表示请求仍在处理，不代表论文不存在。`authentication_required` 需要用户登录科研通后重跑；Sci-Hub 的 ALTCHA 挑战会自动求解，`challenge_required` 仅在自动求解失败时出现。

## Zotero 集成

默认情况下，成功获取后会写入 Zotero：

1. 按 DOI 匹配已有条目并附加 PDF；没有匹配项时创建期刊文章条目。
2. 通过 Zotero 官方文件上传协议上传 PDF。
3. 将附件镜像到本地 Zotero storage 目录。

如果不希望修改 Zotero，请使用 `--no-zotero`。

## 测试

```bash
uv run pytest          # 非 live 测试
uv run pytest -m live  # 需要网络、凭据或浏览器的测试
```

普通测试命令默认排除 live 测试。公开包检查器还会检查 Skill 引用、命名、CLI 元数据和公开树安全性：

```bash
python scripts/check_public_package.py
```

## 开源协议

Copyright © 2026 CaseyTso。

本项目采用 [GNU Affero General Public License v3.0 only](LICENSE)（`AGPL-3.0-only`）。如果你修改本软件并通过网络向用户提供服务，AGPL 要求你向这些用户提供对应源代码。完整条款见 `LICENSE`。

## 致谢

特别感谢 **OpenMinis** 项目
（[OpenMinis/OpenMinis](https://github.com/OpenMinis/OpenMinis)，
<https://openminis.app>，GPL-3.0）及其作者，感谢他们构建了 paper-fetch
所运行的 Minis 智能体应用——Minis WebView 驱动（`ablesci_browser.py`）、
iSH 安装器 profile 和双安装路径都离不开它。

paper-fetch 的部分设计改编自
[scansci-pdf](https://github.com/Rimagination/scansci-pdf)，
Copyright © 2024–2026 scansci-pdf contributors，
基于 [Apache 2.0](https://www.apache.org/licenses/LICENSE-2.0) 协议。

## 合规提示

只使用你有权访问的来源和机构服务。请遵守出版商条款、机构政策、适用的版权法律以及外部服务的使用条款。
