# 📄 基于 RAG 的课程资料问答助手

> 🔗 在线体验：https://ren-rag-pdf.streamlit.app/


> 上传 PDF，让大模型只依据你的文档回答问题 —— 答案流式输出，并附带命中原文片段与相似度分数。
> 当前版本：**v3.8**（Streamlit + Supabase Postgres/pgvector + BGE 中文向量模型 + DeepSeek）

---

## 一、项目介绍

这是一个**检索增强生成（RAG）**的入门级完整实现：把 PDF 切分成文本片段 → 用中文向量模型转成向量**存入数据库（Postgres + pgvector）** → 提问时先检索最相关的片段 → 把片段拼进提示词交给 DeepSeek 生成回答。

它解决的核心问题是：**大模型不知道你的私有文档内容，而且容易"编造"**。本项目通过"先检索、后生成"的方式，让回答有据可依，并在界面上把依据（原文片段 + 页码 + 相似度）直接展示出来，方便核对。

**设计原则**

| 原则 | 体现 |
| --- | --- |
| 职责单一 | 四个文件各管一件事：解析 / 向量库 / 大模型 / 界面 |
| 轻量部署 | 单个 Streamlit 应用即可跑通；本地 sqlite 零配置开发，云端 Postgres 一条连接串 |
| 中文友好 | 全量中文注释；Key 缺失、网络异常、API 报错均给出中文提示，不崩溃 |
| 密钥安全 | 平台 Key 只从 `.env`/Secrets 读取；用户自填 Key 经 Fernet 加密入库，不硬编码、不写日志、不展示 |
| 免费额度（v3.8） | 免费版每天 5 问 / 存 3 个文档 / 每天新传 1 个；付费版 9.9/月为 50 问 / 50 个文档 / 每天新传 5 个 |
| 全面持久化（v3.3） | 文档、片段向量、摘要、会话及其文档绑定全部存数据库，云端重启/休眠后不丢 |

---

## 二、功能特性

- **多 PDF 上传**：一次选择多个文件批量入库，已入库文档自动跳过，不重复向量化；支持逐文档删除后重新上传
- **流式回答**：`stream` 模式逐字输出，打字机效果，无需等待完整响应
- **引用可溯源**：回答下方折叠展示命中的原文片段，标注文件名、页码、相似度百分比
- **自动摘要 + 推荐问题**：文档入库后自动生成全文摘要与 3 个推荐问题，点击即可直接提问
- **账号体系（v3）**：注册 / 登录后才可使用，密码用 PBKDF2 加盐哈希存储，绝不明文
- **数据隔离（v3）**：知识库与会话都按 `user_id` 隔离，用户之间互相看不到对方的文档与会话
- **平台 Key + 可选自填（v3.8）**：注册后默认使用平台统一 DeepSeek Key，不用再填 Key；老用户仍可在设置页填自己的 Key 覆盖，Fernet 加密入库、界面只显示掩码
- **每日额度（v3.8）**：免费/付费等级、每日提问/摘要/上传次数与文档总数限制，顶部实时显示剩余；超限中文拦截，不调 API
- **单文件硬限制（v3.8）**：单个 PDF/PPTX 最多 100 页、20MB，超限直接拒绝并提示按章节拆分
- **会话管理**：新建 / 加载 / 删除历史会话，每轮问答自动保存到数据库（本地 sqlite 或云端 Postgres）
- **中文向量检索**：`BAAI/bge-small-zh-v1.5`，检索 query 自动加 BGE 官方指令前缀
- **三页面结构（v3）**：💬 问答 / 📁 我的文档 / ⚙️ 设置，用 `st.navigation` 组织
- **多轮对话上下文（v3.1）**：追问时自动带上本会话的历史（按字符数截断，最近一轮必留），历史只作背景，回答仍只依据本次检索到的资料
- **支持 PPTX（v3.1）**：除 PDF 外可直接上传 PowerPoint，逐张幻灯片提取文本（含表格单元格）
- **文件持久化（v3.3）**：文档片段与向量存入数据库（云端走 pgvector，本地走 sqlite + numpy 余弦），**重启应用后无需重新上传**；原始文件字节（≤20MB）一并保存
- **会话-文档绑定（v3.3）**：每个会话记住自己用的文档，加载旧会话自动恢复文档列表与摘要；问答检索默认只查本会话选定的文档
- **摘要持久化（v3.3）**：全文摘要与推荐问题随文档存库，换设备/重启后依然可见

---

## 三、支持的文件类型

| 类型 | 解析方式 | 来源展示 |
| --- | --- | --- |
| **PDF（文本版）** | PyPDF2 逐页提取文本 → 清洗 → 约 500 字切分 | 第 X 页 |
| **PDF（扫描件 / 图片页）** | 无文本页自动 OCR：PyMuPDF 2 倍渲染 + RapidOCR 识别 | 第 X 页 |
| **PDF（公式类，需手动勾选）** | 「强制全文 OCR」：忽略文本层，逐页渲染识别 | 第 X 页 |
| **PPTX** | python-pptx 逐张幻灯片提取文本（含表格单元格），复用同一套清洗与切分 | 第 X 张 |

**关于 OCR 兜底（自动）**

- 触发条件很克制：**只有**某页提取不到文本（清洗后少于 10 字）时才走 OCR，有正常文本的页一律不触发，普通 PDF 的速度不受影响。
- 识别引擎**懒加载**：不上传扫描件就完全不加载，不占用内存。
- 模型随 `rapidocr-onnxruntime` 包内置，**运行时不需要联网下载**；单页识别约 1–3 秒。
- 某一页 OCR 失败不会中断整个文件入库，界面会列出被跳过的页码，例如「第 3、5 页为图片且识别失败，已跳过」。
- 云端注意：这三个新依赖合计约 **165MB**（pymupdf 105M + onnxruntime 43M + rapidocr 14M），首次构建时间会变长；若免费版内存吃紧，可从 `requirements.txt` 删除 `pymupdf` 与 `rapidocr-onnxruntime`，OCR 会自动降级为"跳过图片页"并给出提示，其余功能不受影响。

**什么时候需要勾选「强制全文 OCR」**

自动兜底按"整页少于 10 字"判断，有一类文件**走不到 OCR**：**LaTeX 导出的公式类 PDF**
（课件、作业、论文）。这类文件能提取到正文文字（整页字数远超阈值），但公式符号没有
可提取的文本映射，提取后**公式位置是空白**——例如自动机作业里的
「设 $M$ 是一个 DFA，对于任意 $w$，定义 $\hat{\delta}$ 则是正则语言」，
提取结果会变成「设是一个DFA，对于任意，定义则是正则语言」，与公式相关的提问就无法回答。

在「📁 我的文档」页勾选后再入库即可解决：

> ☑ 该文件含大量数学公式或扫描质量差，强制全文 OCR（较慢、更占内存，大文件慎用）

- **适用**：公式多的 LaTeX 课件/作业/论文、扫描质量差但能提取少量文字的文件。
- **不适用**：普通文本 PDF（勾选只会更慢，且 OCR 可能比文本层识别得更差）。
- 勾选后**忽略文本层、逐页渲染识别**，单页识别约 1–3 秒；入库完成会提示
  「已强制全文 OCR，共 N 页通过识别入库」。单页 OCR 失败会回退到该页提取到的文本，
  不会中断整个文件。
- ⚠️ **RapidOCR 是通用文字识别，不是数学 OCR**：对复杂公式仍可能认不全——
  上下标、希腊字母（如 $\delta$、$\Sigma$）、根号与矩阵容易识别错误或丢失，
  简单行内公式和符号通常可以认出。公式密集型文档的建议仍是后续引入
  专业公式识别（如 Pix2Text / LaTeX-OCR），本项目暂不引入该重依赖。
- 该开关仅对 PDF 生效，PPTX 本身有文本结构，不受影响。

---

## 四、功能截图

> ⚠️ 截图待补充：启动应用后按下面场景截图，覆盖 `docs/screenshots/` 中同名文件即可（建议宽度 1200px 左右）。

| 场景 | 预览 |
| --- | --- |
| 文档上传与自动摘要 | ![文档上传与摘要](docs/screenshots/upload_summary.png) |
| 流式问答与引用来源 | ![流式问答](docs/screenshots/chat_stream.png) |
| 命中原文与相似度 | ![命中片段](docs/screenshots/sources.png) |
| 侧边栏会话管理 | ![会话管理](docs/screenshots/sessions.png) |

---

## 五、技术架构

### 4.1 技术栈

| 层次 | 选型 | 说明 |
| --- | --- | --- |
| 界面 | Streamlit | 纯 Python 写 Web UI，无需前端代码 |
| 文档解析 | PyPDF2 + python-pptx + PyMuPDF | PDF 逐页提取、PPTX 逐张幻灯片提取；扫描件页渲染后 OCR |
| OCR | rapidocr-onnxruntime（RapidOCR） | 中文识别，模型随包内置，懒加载 |
| 向量模型 | sentence-transformers · `BAAI/bge-small-zh-v1.5` | 中文语义向量，约 100MB，输出 512 维 |
| 向量存储 | 数据库 `document_chunks` 表 | 云端 Postgres + **pgvector**（SQL 余弦距离）；本地 sqlite 存 float32 字节、numpy 算余弦，两条路径结果一致 |
| 大模型 | DeepSeek（`deepseek-flash`） | OpenAI 兼容 SDK，支持流式 |
| 数据库 | SQLAlchemy + sqlite / Postgres | 本地 sqlite，云端接 Supabase Postgres |
| 密码与加密 | hashlib.pbkdf2_hmac + cryptography.Fernet | 密码加盐哈希，用户 Key 对称加密 |
| 配置 | python-dotenv | 从 `.env` 读取 `DATABASE_URL` / `APP_SECRET_KEY` |

### 4.2 模块职责

| 文件 | 职责 | 关键接口 |
| --- | --- | --- |
| `app.py` | 界面与交互：登录注册、三页导航、流式聊天、来源展示 | `render_auth_page()` / `page_chat()` / `page_documents()` / `page_settings()` |
| `loader.py` | PDF / PPTX 文本提取与切分（v3.1：新增 PPTX 与 OCR 兜底） | `load_and_split_pdf(file, ocr_report=)` / `load_and_split_pptx(file)` / `load_and_split_file(file, name)` → `[{text, page}]` |
| `vectorstore.py` | 向量化与相似检索（按用户隔离，元数据含 `doc_type`） | `add_document(user_id, ...)` / `similarity_search(user_id, ...)` / `delete_document(user_id, ...)` |
| `qa.py` | 调用 DeepSeek 生成回答（Key 由调用方传入，v3.1 支持 history） | `stream_answer(q, ctx, history=, api_key=)` / `truncate_history()` / `generate_doc_brief(...)` |
| `db.py` | v3 新增：表定义与数据读写 | `init_db()` / `create_session()` / `list_sessions(user_id)` / `save_session()` / `delete_session()` |
| `auth.py` | v3 新增：账号体系与密钥安全 | `register()` / `login()` / `hash_password()` / `encrypt_key()` / `mask_key()` |

### 4.3 数据流转

```mermaid
flowchart TD
    A[注册 / 登录] --> A1[(users 表<br/>密码哈希 + 加密 Key)]
    A1 --> B[上传 PDF]
    B --> C[loader.py<br/>PyPDF2 提取 + 清洗]
    C --> D[切分：约 500 字/段<br/>段间约 50 字重叠]
    D --> E[vectorstore.py<br/>BGE 中文向量化]
    E --> F[(数据库 document_chunks<br/>云端 pgvector / 本地 sqlite)]

    G[用户提问] --> H[取用户解密后的 Key]
    H --> I[query 向量化<br/>加 BGE 指令前缀]
    F --> J[相似检索 Top-4<br/>仅限本人 collection]
    I --> J
    J --> K[qa.py 拼接提示词]
    K --> L[DeepSeek deepseek-flash]
    L --> M[流式输出回答]
    J --> N[折叠展示原文片段<br/>+ 页码 + 相似度]
    M --> O[(sessions 表<br/>按 user_id 隔离)]
```

### 4.4 文本切分策略

1. 清洗：统一换行符、合并段内换行、压缩多余空格
2. 按中文句末标点（`。！？；`）切句
3. 逐句累积到约 **500 字**成段；新段落从上一自然段尾取约 **50 字**作为重叠，避免截断语义
4. 单句超过 500 字时，退化为 500/50 的**字符滑动窗口**硬切兜底

---

## 六、本地运行步骤

### 1. 准备环境

```bash
git clone <你的仓库地址>
cd pdf_qa_app
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

> 依赖：`streamlit`、`PyPDF2`、`sentence-transformers`、`openai`、`python-dotenv`、`sqlalchemy`、`psycopg[binary]`、`cryptography`、`python-pptx`、`pymupdf`、`rapidocr-onnxruntime`（v3.3 起 chromadb 已移除）

### 3. 配置环境变量

```bash
cp .env.example .env
```

v3.8 本地至少需要 `APP_SECRET_KEY`（加密用户自填 Key 用）和平台 `DEEPSEEK_API_KEY`：

```bash
# 生成一把随机密钥，把输出粘到 .env 里
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

```ini
APP_SECRET_KEY=粘贴上面命令生成的字符串
DEEPSEEK_API_KEY=sk-你自己的DeepSeek官方Key
TURNSTILE_SITE_KEY=你的Cloudflare-Turnstile-Site-Key
TURNSTILE_SECRET_KEY=你的Cloudflare-Turnstile-Secret-Key
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=no-reply@example.com
SMTP_PASSWORD=邮箱SMTP授权码
SMTP_FROM=no-reply@example.com
SMTP_SECURITY=starttls
```

`DATABASE_URL` 本地**可以留空**，程序会自动使用项目下的 sqlite 文件 `app.db`。

> v3.8 说明：平台统一使用 `DEEPSEEK_API_KEY`，必须是**你自己的 DeepSeek 官方 Key**
> （接口地址固定 `https://api.deepseek.com`，模型固定 `deepseek-flash`）；
> 不要使用同学提供的临时网关域名。用户自填 Key 变成可选覆盖项。
> 也可以直接用系统环境变量配置（优先级高于 `.env`）。

### 4. 启动应用

```bash
streamlit run app.py
```

浏览器会自动打开 `http://localhost:8501`。

### 5. 开始使用

1. 首次打开是登录页：切到「注册」创建账号（用户名 3–20 位字母/数字/下划线，密码至少 8 位）
2. 登录后直接可用：默认走平台 Key，**不需要填写任何 Key**；老用户想用自己的 Key 可在「⚙️ 设置」页选填
3. 进「📁 我的文档」上传一个或多个 PDF → 点「解析并入库」
   （首次会自动从 `hf-mirror` 下载向量模型约 100MB，请耐心等待）
4. 回「💬 问答」页查看自动摘要与推荐问题，或直接在底部输入框提问；
   回答流式输出，展开「📚 命中原文片段与相似度」核对依据

---

## 七、Streamlit Cloud 部署步骤

### 1. 推送代码到 GitHub

确保根目录含 `app.py` 与 `requirements.txt`，并确认 `.env` **没有**被提交（已在 `.gitignore` 中）：

```bash
git status          # 确认无 .env
git add . && git commit -m "feat: RAG PDF QA v2" && git push
```

### 2. 创建应用

1. 打开 [share.streamlit.io](https://share.streamlit.io/)，用 GitHub 账号登录
2. 点 **New app** → 选择仓库、分支
3. **Main file path** 填 `app.py`（本仓库根目录即项目根目录）
4. Python version 选 **3.10+**

### 3. 配置 Secrets（关键）

在应用页面 → **Settings → Secrets** 填入（TOML 格式），三个变量都必填：

```toml
DATABASE_URL = "postgresql://postgres.xxxx:你的密码@aws-0-xxx.pooler.supabase.com:5432/postgres"
APP_SECRET_KEY = "你的Fernet密钥"
DEEPSEEK_API_KEY = "sk-你自己的DeepSeek官方Key"
TURNSTILE_SITE_KEY = "你的Cloudflare-Turnstile-Site-Key"
TURNSTILE_SECRET_KEY = "你的Cloudflare-Turnstile-Secret-Key"
SMTP_HOST = "smtp.example.com"
SMTP_PORT = "587"
SMTP_USER = "no-reply@example.com"
SMTP_PASSWORD = "邮箱SMTP授权码"
SMTP_FROM = "no-reply@example.com"
SMTP_SECURITY = "starttls"
```

保存后应用会自动重启。这三个变量的详细获取步骤见「八、云端部署需要配置的 Secrets」。

> v3.8 起平台统一使用 `DEEPSEEK_API_KEY`（官方 `api.deepseek.com`，模型 `deepseek-flash`）；
> `APP_PASSWORD` 已废弃，门禁改由账号登录取代。

### 4. 等待首次构建

- 云端会安装依赖并**下载 BGE 向量模型**（约 100MB），首次冷启动可能需要数分钟
- 若下载 HuggingFace 卡顿，代码已内置 `HF_ENDPOINT=https://hf-mirror.com` 镜像

### 5. 云端注意事项

| 事项 | 说明 |
| --- | --- |
| 知识库**持久**（v3.3） | 文档片段与向量存在 Supabase Postgres（pgvector），容器重启/休眠唤醒后**无需重新上传** |
| 原始文件 | 单文件最多 100 页 / 20MB，超限直接拒绝；在限制内的原始文件字节随文档入库 |
| 会话历史**持久** | 会话及其文档绑定存在 Supabase Postgres 里，容器重启不丢（v3 相比 v2 的本地 JSON 是一大改进） |
| 资源限制 | 免费版约 1GB 内存；`torch + bge-small-zh-v1.5` 较吃紧，不宜上传超大 PDF（v3.3 移除 Chroma 后内存压力更小） |
| 密钥安全 | `APP_SECRET_KEY` 与数据库连接串只走 Secrets，不要把 `.env` 提交到仓库 |

---

## 八、云端部署需要配置的 Secrets

v3.8 需要三个环境变量，缺一个都会影响使用：

| 变量 | 作用 | 获取方式 |
| --- | --- | --- |
| `DATABASE_URL` | 存用户账号、每日用量、加密后的 Key、文档与会话记录 | Supabase 项目的 Postgres 连接串（见下方步骤） |
| `APP_SECRET_KEY` | 用 Fernet 加密每位用户的 API Key | 本地执行 `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `DEEPSEEK_API_KEY` | 平台统一调用的官方 DeepSeek Key（注册后即可用，无需用户自填） | [platform.deepseek.com](https://platform.deepseek.com/) 自行创建；必须是官方 Key，不要用临时网关 |
| `TURNSTILE_SITE_KEY` / `TURNSTILE_SECRET_KEY` | 注册页人机验证（v3.9 防刷注册） | Cloudflare Dashboard → Turnstile 创建站点后获得；Secret Key 只放 Secrets，不写进代码/日志 |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` / `SMTP_FROM` | 发送注册邮箱验证码 | 邮箱服务商的 SMTP 信息；密码填授权码，不要填网页登录密码 |

> ⚠️ `APP_SECRET_KEY` 一旦更换，之前用户保存的 Key 会解密失败，
> 届时需要请用户到「⚙️ 设置」页重新填写。

### 获取 Supabase 免费 Postgres 连接串（约 3 分钟）

1. 打开 [supabase.com](https://supabase.com/) → 用 GitHub 登录 → **New project**
2. 填项目名、设置数据库密码（**保存好这个密码**），Region 建议选 **Southeast Asia (Singapore)**，免费套餐即可
3. 项目创建完成后，左下角 **Project Settings** → **Database**
4. 在 **Connection string** 区切换到 **URI** 标签，复制那串 `postgresql://postgres.xxxx:[YOUR-PASSWORD]@...` 的链接
5. 把链接里的 `[YOUR-PASSWORD]` 换成你在第 2 步设置的密码
6. **如果 Supabase 给你的是下面这种 `.env` 模板，不能直接照抄**（这是给 Prisma 用的）：

   ```ini
   DATABASE_URL="postgresql://postgres.xxxx:[YOUR-PASSWORD]@//aws-0-ap-northeast-1.pooler.supabase.com/:6543/postgres?pgbouncer=true"
   DIRECT_URL="postgresql://postgres.xxxx:[YOUR-PASSWORD]@//aws-0-ap-northeast-1.pooler.supabase.com/:5432/postgres"
   ```

   改法三条：
   - **用 `DIRECT_URL` 那一行（端口 5432，session 模式）**，不要用 6543 的事务模式 pooler；本项目用 SQLAlchemy 建表 + 会话级查询，事务模式容易出现预编译语句兼容问题
   - **删掉主机名前后的 `//` 和 `/`**，写成 `...@aws-0-ap-northeast-1.pooler.supabase.com:5432/postgres`
   - **不需要 `DIRECT_URL`**，本项目只认 `DATABASE_URL` 一个变量

7. 粘贴到 Streamlit Cloud 的 Secrets：

```toml
DATABASE_URL = "postgresql://postgres.xxxx:你的密码@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres"
```

首次部署时程序会自动建 `users`、`sessions`、`documents`、`document_chunks`、`session_documents`、`daily_usage` 等表，不需要手工执行 SQL；老库的 `users` 表会自动补 `tier` 列（默认 `free`）。

> 连接失败排查：确认密码里的特殊字符已 URL 编码（如 `@` 要写成 `%40`）；
> 若提示 SSL 相关错误，可在连接串末尾加 `?sslmode=require`；
> 若提示连接数耗尽，检查是否有其他客户端占用了 Supabase 免费版极少的并发连接（本项目已把连接池限制在 8 条以内）。

### 用户额度与人工开通付费（v3.8）

额度定稿（2026-11-09）：

| 项目 | 免费版 `free` | 付费版 `paid`（9.9/月） |
| --- | --- | --- |
| 每日提问 | 5 次 | 50 次 |
| 当前最多存文档 | 3 个（删掉即腾位置） | 50 个 |
| 每日新传文档 | 1 个 | 5 个 |
| 每日摘要 | 5 次（防删传来回刷） | 随文档上传生成，日上限 20 次 |

- 用量按**北京时间**日期记在 `daily_usage` 表，同一用户同一天只有一行；查当天行即可实现 0 点清零，不需要定时任务。
- 提问、生成摘要、上传文档前都会先查额度；超限直接中文提示，不调用 API、不扣次数。
- 单个文件最多 **100 页 / 20MB**，超限提示「文件过大（超过 100 页或 20MB），请按章节拆分后上传」。
- 付费先走人工（爱发电 9.9/月或微信转账），页面不接支付。确认收款后手动改 `users.tier`：

```sql
-- 升级为付费版
UPDATE users SET tier = 'paid' WHERE username = '用户填的用户名';
-- 降级回免费版
UPDATE users SET tier = 'free' WHERE username = '用户填的用户名';
```

也可以在项目根目录执行：

```bash
python -c "import db; print(db.set_user_tier_by_username('用户填的用户名', 'paid'))"
```

---

## 九、目录结构

```
pdf_qa_app/
├── app.py                     # Streamlit 界面（登录 + 问答 / 我的文档 / 设置 三页）
├── loader.py                  # PDF / PPTX 解析与文本切分（v3.1：+PPTX、+OCR 兜底）
├── vectorstore.py             # 向量化与检索（v3：按 user_id 隔离 collection）
├── qa.py                      # DeepSeek 调用（v3：Key 由调用方传入；v3.1：多轮 history）
├── auth.py                    # v3 新增：注册登录、密码哈希、Key 加解密与掩码
├── db.py                      # v3 新增：SQLAlchemy 数据访问（users / sessions 表）
├── quota.py                   # v3.8 新增：等级、每日用量与额度拦截（free / paid）
├── requirements.txt           # 依赖清单
├── .env                       # 本地配置（含 APP_SECRET_KEY，不提交）
├── .env.example               # 配置模板
├── .gitignore
├── .streamlit/config.toml     # v3 新增：界面主题（深蓝主色 / 白色卡片 / 浅灰侧栏）
├── 修改说明_v2.md              # v1 → v2 改动说明
├── 修改说明_v3.md              # v2 → v3 改动说明
├── 修改说明_v3.1.md            # v3 → v3.1 改动说明
├── 修改说明_v3.2.md            # v3.1 → v3.2 改动说明（强制全文 OCR）
├── chroma_db/                 # （已废弃）v3.2 及以前的本地向量库目录，可手动删除
├── app.db                     # 本地 sqlite（不配 DATABASE_URL 时自动生成，不提交）
└── tests/                     # 单元测试
    ├── conftest.py            # sys.path + 临时数据库 / Fernet 密钥 fixture
    ├── test_loader.py         # 文本切分（15 例）
    ├── test_qa.py             # JSON 解析与错误处理（13 例）
    ├── test_auth.py           # v3 新增：密码哈希 / Key 加解密 / 掩码（16 例）
    ├── test_sessions_isolation.py  # v3 新增：会话隔离与越权防护（6 例）
    ├── test_pptx.py           # v3.1 新增：PPTX 解析与按后缀分发（8 例）
    ├── test_multiturn.py      # v3.1 新增：历史截断与四段提示词（12 例）
    ├── test_ocr_fallback.py   # v3.1 新增：OCR 兜底与降级（8 例，含 1 例默认跳过）
    ├── test_force_ocr.py      # v3.2 新增：强制全文 OCR 与失败回退（13 例，含 1 例默认跳过）
    └── test_persistence.py    # v3.3 新增：文档/片段/摘要持久化、会话绑定与越权（18 例）
```

---

## 十、运行测试

测试全部使用假数据（mock），**不会调用真实 DeepSeek API、不会下载向量模型**。

```bash
# 安装（若使用项目 venv）
pip install pytest

# 在项目根目录 pdf_qa_app 下运行
pytest tests -v                      # 显示每个用例名
pytest tests -q                      # 只显示汇总
pytest tests/test_loader.py -v       # 只跑切分相关用例
pytest tests/test_qa.py -k JSON      # 只跑名字含 JSON 的用例
```

当前共 **255 passed / 2 skipped**，覆盖 loader 的文本清洗/切句/重叠切分/硬切兜底、PPTX 解析、OCR 自动兜底与强制 OCR、100 页/20MB 硬限制；qa 的 JSON 解析（含代码块围栏、非法 JSON 降级）、错误处理（缺 Key / 401 / 网络失败）、流式输出拼接与多轮历史截断；auth 的密码哈希与 Key 加解密；会话按用户隔离与越权防护；v3.8 的 free/paid 每日额度、文档总数、上传与摘要次数拦截，以及平台 Key 与老用户自填 Key 的优先级。

> 2 个 skipped 是真实 OCR 引擎的冒烟用例，默认跳过以免加载识别模型；设置环境变量 `RUN_REAL_OCR=1` 后可启用。

## 十一、常见问题

**Q：提示"平台 Key 暂未配置"？**
A：确认 Streamlit Secrets 或 `.env` 里有 `DEEPSEEK_API_KEY=sk-xxx`（必须是 DeepSeek 官方 Key），改完后重启应用；也可以在设置页临时填写自己的 Key。

**Q：提示"今日提问次数已用完"或"文档存储已满"？**
A：免费版每天 5 问、最多存 3 个文档、每天新传 1 个；删掉不需要的文档可腾出存储位置。用量按北京时间 0 点恢复。付费版 9.9/月（50 问 / 50 个文档 / 每天新传 5 个），联系管理员人工开通。

**Q：PDF 上传后提示"未提取到文本"？**
A：说明整份文件都没有可用的文本层（纯扫描件），且 OCR 也没识别出来。可确认 `pymupdf` 与
`rapidocr-onnxruntime` 两个依赖已安装；若日志提示 OCR 引擎不可用，参考「十、运行测试」里
`RUN_REAL_OCR=1` 的说明排查。

**Q：PDF 能入库，但公式相关的问题答不上来？**
A：大概率是 LaTeX 导出的公式类 PDF——正文能提取，公式位置却是空白。删除该文档后，
在「📁 我的文档」页勾选「强制全文 OCR」重新入库即可。注意 RapidOCR 是通用文字识别，
对复杂公式（上下标、希腊字母、矩阵）仍可能认不全。

**Q：回答不准确或说"根据现有资料无法回答"？**
A：检索未命中有效片段。可在 `app.py` 中把 `similarity_search(question, top_k=4)` 的 `top_k` 调大（如 6），或检查文档是否已成功入库。

**Q：想换更强的模型？**
A：修改 `qa.py` 顶部的 `MODEL` 常量即可（如 `deepseek-reasoner`），接口地址无需改动。

**Q：如何清空知识库？**
A：v3.3 起知识库在数据库里：在「📁 我的文档」页逐个删除即可；想彻底清空可直接清空数据库的 documents / document_chunks 表（云端在 Supabase SQL 编辑器执行 `TRUNCATE document_chunks, documents, session_documents CASCADE;`）。
