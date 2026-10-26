# 📄 基于 RAG 的 PDF 智能问答助手

> 上传 PDF，让大模型只依据你的文档回答问题 —— 答案流式输出，并附带命中原文片段与相似度分数。
> 当前版本：**v2**（Python + Streamlit + Chroma + BGE 中文向量模型 + DeepSeek）

---

## 一、项目介绍

这是一个**检索增强生成（RAG）**的入门级完整实现：把 PDF 切分成文本片段 → 用中文向量模型转成向量存入 Chroma → 提问时先检索最相关的片段 → 把片段拼进提示词交给 DeepSeek 生成回答。

它解决的核心问题是：**大模型不知道你的私有文档内容，而且容易"编造"**。本项目通过"先检索、后生成"的方式，让回答有据可依，并在界面上把依据（原文片段 + 页码 + 相似度）直接展示出来，方便核对。

**设计原则**

| 原则 | 体现 |
| --- | --- |
| 职责单一 | 四个文件各管一件事：解析 / 向量库 / 大模型 / 界面 |
| 轻量无后端 | 不使用数据库、不做登录注册，向量库与会话历史均为本地文件 |
| 中文友好 | 全量中文注释；Key 缺失、网络异常、API 报错均给出中文提示，不崩溃 |
| 密钥安全 | API Key 只从 `.env` 读取，不硬编码进代码，已被 `.gitignore` 忽略 |

---

## 二、功能特性

- **多 PDF 上传**：一次选择多个文件批量入库，已入库文档自动跳过，不重复向量化；支持逐文档删除后重新上传
- **流式回答**：`stream` 模式逐字输出，打字机效果，无需等待完整响应
- **引用可溯源**：回答下方折叠展示命中的原文片段，标注文件名、页码、相似度百分比
- **自动摘要 + 推荐问题**：文档入库后自动生成全文摘要与 3 个推荐问题，点击即可直接提问
- **账号体系（v3）**：注册 / 登录后才可使用，密码用 PBKDF2 加盐哈希存储，绝不明文
- **数据隔离（v3）**：知识库与会话都按 `user_id` 隔离，用户之间互相看不到对方的文档与会话
- **各自的 Key（v3）**：每位用户在「⚙️ 设置」页填写自己的 DeepSeek Key，Fernet 加密入库，界面只显示掩码
- **会话管理**：新建 / 加载 / 删除历史会话，每轮问答自动保存到数据库（本地 sqlite 或云端 Postgres）
- **中文向量检索**：`BAAI/bge-small-zh-v1.5`，检索 query 自动加 BGE 官方指令前缀
- **三页面结构（v3）**：💬 问答 / 📁 我的文档 / ⚙️ 设置，用 `st.navigation` 组织
- **多轮对话上下文（v3.1）**：追问时自动带上本会话的历史（按字符数截断，最近一轮必留），历史只作背景，回答仍只依据本次检索到的资料
- **支持 PPTX（v3.1）**：除 PDF 外可直接上传 PowerPoint，逐张幻灯片提取文本（含表格单元格）

---

## 三、支持的文件类型

| 类型 | 解析方式 | 来源展示 |
| --- | --- | --- |
| **PDF（文本版）** | PyPDF2 逐页提取文本 → 清洗 → 约 500 字切分 | 第 X 页 |
| **PDF（扫描件 / 图片页）** | 无文本页自动 OCR：PyMuPDF 2 倍渲染 + RapidOCR 识别 | 第 X 页 |
| **PPTX** | python-pptx 逐张幻灯片提取文本（含表格单元格），复用同一套清洗与切分 | 第 X 张 |

**关于 OCR 兜底**

- 触发条件很克制：**只有**某页提取不到文本（清洗后少于 10 字）时才走 OCR，有正常文本的页一律不触发，普通 PDF 的速度不受影响。
- 识别引擎**懒加载**：不上传扫描件就完全不加载，不占用内存。
- 模型随 `rapidocr-onnxruntime` 包内置，**运行时不需要联网下载**；单页识别约 1–3 秒。
- 某一页 OCR 失败不会中断整个文件入库，界面会列出被跳过的页码，例如「第 3、5 页为图片且识别失败，已跳过」。
- 云端注意：这三个新依赖合计约 **165MB**（pymupdf 105M + onnxruntime 43M + rapidocr 14M），首次构建时间会变长；若免费版内存吃紧，可从 `requirements.txt` 删除 `pymupdf` 与 `rapidocr-onnxruntime`，OCR 会自动降级为"跳过图片页"并给出提示，其余功能不受影响。

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
| 向量库 | Chroma（`PersistentClient`） | 每个用户一个 collection，余弦距离检索 |
| 大模型 | DeepSeek（`deepseek-chat`） | OpenAI 兼容 SDK，支持流式 |
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
    E --> F[(Chroma<br/>pdf_chunks_u{user_id})]

    G[用户提问] --> H[取用户解密后的 Key]
    H --> I[query 向量化<br/>加 BGE 指令前缀]
    F --> J[相似检索 Top-4<br/>仅限本人 collection]
    I --> J
    J --> K[qa.py 拼接提示词]
    K --> L[DeepSeek deepseek-chat]
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

> 依赖：`streamlit`、`PyPDF2`、`sentence-transformers`、`chromadb`、`openai`、`python-dotenv`

### 3. 配置环境变量

```bash
cp .env.example .env
```

v3 只需要配一行 `APP_SECRET_KEY`（用于加密保存每位用户的 DeepSeek Key）：

```bash
# 生成一把随机密钥，把输出粘到 .env 里
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

```ini
APP_SECRET_KEY=粘贴上面命令生成的字符串
```

`DATABASE_URL` 本地**可以留空**，程序会自动使用项目下的 sqlite 文件 `app.db`。

> v3 说明：`DEEPSEEK_API_KEY` 与 `APP_PASSWORD` 都已废弃——
> API Key 改由每位用户在「⚙️ 设置」页填写，门禁改由账号登录取代。
> 也可以直接用系统环境变量配置（优先级高于 `.env`）。

### 4. 启动应用

```bash
streamlit run app.py
```

浏览器会自动打开 `http://localhost:8501`。

### 5. 开始使用

1. 首次打开是登录页：切到「注册」创建账号（用户名 3–20 位字母/数字/下划线，密码至少 8 位）
2. 登录后进「⚙️ 设置」页，填入你的 DeepSeek Key（[申请地址](https://platform.deepseek.com/)），加密保存
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

在应用页面 → **Settings → Secrets** 填入（TOML 格式），两个变量都必填：

```toml
DATABASE_URL = "postgresql://postgres.xxxx:你的密码@aws-0-xxx.pooler.supabase.com:5432/postgres"
APP_SECRET_KEY = "你的Fernet密钥"
```

保存后应用会自动重启。这两个变量的详细获取步骤见「七、云端部署需要配置的 Secrets」。

> v3 起**不再需要** `DEEPSEEK_API_KEY`（改由用户自己填写）和 `APP_PASSWORD`（改由登录体系取代）。

### 4. 等待首次构建

- 云端会安装依赖并**下载 BGE 向量模型**（约 100MB），首次冷启动可能需要数分钟
- 若下载 HuggingFace 卡顿，代码已内置 `HF_ENDPOINT=https://hf-mirror.com` 镜像

### 5. 云端注意事项

| 事项 | 说明 |
| --- | --- |
| 知识库不持久 | 云端容器重启后 `chroma_db/` 会被清空，需要重新上传 PDF（免费版无持久磁盘） |
| 会话历史**持久** | 会话存在 Supabase Postgres 里，容器重启不丢（v3 相比 v2 的本地 JSON 是一大改进） |
| 资源限制 | 免费版约 1GB 内存；`torch + bge-small-zh-v1.5 + Chroma` 比较吃紧，不宜上传超大 PDF |
| 密钥安全 | `APP_SECRET_KEY` 与数据库连接串只走 Secrets，不要把 `.env` 提交到仓库 |

---

## 八、云端部署需要配置的 Secrets

v3 需要两个环境变量，缺一个应用就起不来：

| 变量 | 作用 | 获取方式 |
| --- | --- | --- |
| `DATABASE_URL` | 存用户账号、加密后的 Key 和会话记录 | Supabase 项目的 Postgres 连接串（见下方步骤） |
| `APP_SECRET_KEY` | 用 Fernet 加密每位用户的 API Key | 本地执行 `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |

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

首次部署时程序会自动建 `users` 和 `sessions` 两张表，不需要手工执行 SQL。

> 连接失败排查：确认密码里的特殊字符已 URL 编码（如 `@` 要写成 `%40`）；
> 若提示 SSL 相关错误，可在连接串末尾加 `?sslmode=require`；
> 若提示连接数耗尽，检查是否有其他客户端占用了 Supabase 免费版极少的并发连接（本项目已把连接池限制在 8 条以内）。

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
├── requirements.txt           # 依赖清单
├── .env                       # 本地配置（含 APP_SECRET_KEY，不提交）
├── .env.example               # 配置模板
├── .gitignore
├── .streamlit/config.toml     # v3 新增：界面主题（深蓝主色 / 白色卡片 / 浅灰侧栏）
├── 修改说明_v2.md              # v1 → v2 改动说明
├── 修改说明_v3.md              # v2 → v3 改动说明
├── 修改说明_v3.1.md            # v3 → v3.1 改动说明
├── chroma_db/                 # 向量库（运行时生成，不提交）
├── app.db                     # 本地 sqlite（不配 DATABASE_URL 时自动生成，不提交）
└── tests/                     # 单元测试
    ├── conftest.py            # sys.path + 临时数据库 / Fernet 密钥 fixture
    ├── test_loader.py         # 文本切分（15 例）
    ├── test_qa.py             # JSON 解析与错误处理（13 例）
    ├── test_auth.py           # v3 新增：密码哈希 / Key 加解密 / 掩码（16 例）
    ├── test_sessions_isolation.py  # v3 新增：会话隔离与越权防护（6 例）
    ├── test_pptx.py           # v3.1 新增：PPTX 解析与按后缀分发（8 例）
    ├── test_multiturn.py      # v3.1 新增：历史截断与四段提示词（12 例）
    └── test_ocr_fallback.py   # v3.1 新增：OCR 兜底与降级（8 例，含 1 例默认跳过）
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

当前用例：28 个，覆盖 `loader.py` 的文本清洗/切句/重叠切分/硬切兜底，以及 `qa.py` 的 JSON 解析（含代码块围栏、非法 JSON 降级）、错误处理（缺 Key / 401 / 网络失败）与流式输出拼接。

## 十一、常见问题

**Q：提示"未检测到 API Key"？**
A：确认 `.env` 与 `app.py` 同级、文件名为 `.env`（不是 `.env.txt`）、内容格式为 `DEEPSEEK_API_KEY=sk-xxx`，改完后重启应用。

**Q：PDF 上传后提示"未提取到文本"？**
A：说明是扫描件（图片型 PDF）。PyPDF2 只能读取文本层，需要接入 OCR（如 PaddleOCR / pytesseract）后再入库。

**Q：回答不准确或说"根据现有资料无法回答"？**
A：检索未命中有效片段。可在 `app.py` 中把 `similarity_search(question, top_k=4)` 的 `top_k` 调大（如 6），或检查文档是否已成功入库。

**Q：想换更强的模型？**
A：修改 `qa.py` 顶部的 `MODEL` 常量即可（如 `deepseek-reasoner`），接口地址无需改动。

**Q：如何清空知识库？**
A：删除 `chroma_db/` 目录后重启应用；删除 `sessions/` 可清空全部历史会话。
