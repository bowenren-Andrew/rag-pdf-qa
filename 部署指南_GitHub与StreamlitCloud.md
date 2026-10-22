# 部署指南：GitHub → Streamlit Cloud → 访问密码

本文档记录本项目从本地代码到「公网可访问 + 密码保护」的完整过程。
按文档顺序操作即可，每一步都写明了"在哪点、填什么、看到什么算成功"。

---

## 零、当前进度（已帮你完成的部分）

| 项目 | 状态 | 说明 |
| --- | --- | --- |
| Git 仓库初始化 | ✅ 已完成 | 仓库根目录 = `pdf_qa_app/`（不是外层工作区） |
| `.gitignore` / `.gitattributes` | ✅ 已完成 | 已排除 `.env`、`chroma_db/`、`sessions/`、`.streamlit/secrets.toml` |
| 首次提交 | ✅ 已完成 | `feat: 基于 RAG 的 PDF 问答系统 v2` |
| GitHub 仓库创建 | ✅ 已完成 | **私有仓库**：https://github.com/bowenren-Andrew/rag-pdf-qa |
| 代码推送 | ✅ 已完成 | 分支 `master`，已同步远程 |
| 敏感文件核查 | ✅ 已确认 | 远程仓库**只有** `.env.example`（模板），真实 `.env` 未上传 |
| 访问密码功能 | ✅ 已写入 `app.py` | 可选开关，不配置就完全不启用 |
| Streamlit Cloud 部署 | ⬜ 待你操作 | 见「二、部署到 Streamlit Cloud」，约 5 分钟 |
| 访问密码配置 | ⬜ 待你操作 | 见「三、设置访问密码」，1 分钟 |

已推送的文件清单（19 项）：

```
.env.example  .gitignore  .gitattributes  README.md
app.py  loader.py  vectorstore.py  qa.py  requirements.txt
tests/conftest.py  tests/test_loader.py  tests/test_qa.py
docs/RAG项目介绍.pptx  docs/screenshots/.gitkeep
代码讲解文档.md  修改说明_v2.md
```

---

## 一、GitHub：以后怎么更新代码

仓库已经建好，日常只需要三条命令。在 `pdf_qa_app/` 目录下执行：

```bash
git add .
git commit -m "说明你改了什么"
git push
```

> **网络提示（本机专用）**：这台机器 `github.com` 直连不通，必须走代理。
> 若 `git push` 卡住或报超时，用下面这条显式指定 Clash（端口 7897）：
>
> ```bash
> git -c http.proxy=http://127.0.0.1:7897 -c https.proxy=http://127.0.0.1:7897 push
> ```
>
> 已安装的 `gh` CLI 也已登录（`bowenren-Andrew`），可用 `gh repo view --web` 快速打开仓库页面。

**推送前必做的自查**（防止密钥泄露）：

```bash
git status --short          # 确认列表里没有 .env
git check-ignore -v .env    # 有输出 = 已被忽略，正确
```

若曾经误提交过 `.env`，立刻做两件事：① 到 DeepSeek 控制台**吊销旧 Key 重新生成**；
② `git rm --cached .env && git commit -m "remove .env" && git push`（注意历史里仍可能残留，改 Key 才是根本）。

---

## 二、部署到 Streamlit Cloud（约 5 分钟）

### 步骤 1：登录

打开 https://share.streamlit.io/ → 点 **Sign in with GitHub** → 授权。

### 步骤 2：创建应用

点右上角 **New app**，表单这样填：

| 字段 | 填什么 |
| --- | --- |
| Repository | `bowenren-Andrew/rag-pdf-qa` |
| Branch | `master` |
| **Main file path** | **`app.py`**（仓库根目录就是项目根目录，不要再写 `pdf_qa_app/`） |
| App URL | 保持自动生成即可，也可自定义成好记的前缀 |

> **私有仓库首次部署**：会弹出 GitHub 授权页，要求允许 Streamlit 访问你的私有仓库。
> 点授权即可（GitHub → Settings → Applications → Streamlit → Configure，可随时撤销）。
> Streamlit Community Cloud 免费版支持 **1 个私有仓库应用**，足够本项目使用。

### 步骤 3：设置 Python 版本（可选但建议）

点 **Advanced settings** → Python version 选 **3.11**（与本地 3.13 一致或略低，兼容性更稳）。

### 步骤 4：点 Deploy，等待首次构建

首次构建流程：装依赖（`requirements.txt`）→ 下载 BGE 向量模型（约 100MB）→ 启动。

- 预计 **3–8 分钟**，底部日志区可以看到实时进度
- 代码已内置 `HF_ENDPOINT=https://hf-mirror.com`，云端走国内镜像，一般不会卡在模型下载
- 看到 🎈 气球动画 = 启动成功

### 步骤 5：拿到访问地址

部署成功后地址形如：

```
https://bowenren-andrew-rag-pdf-qa-app-xxxxxx.streamlit.app
```

以 Streamlit 控制台实际显示为准，复制保存。

---

## 三、设置访问密码（1 分钟）

### 3.1 在云端配置

1. 打开你的应用 → 右下角 **Manage app**（或右上角 `⋮`）→ **Settings**
2. 左侧选 **Secrets**
3. 填入 TOML 格式内容：

```toml
DEEPSEEK_API_KEY = "sk-你的DeepSeekKey"
APP_PASSWORD = "你想设置的访问密码"
```

4. 点 **Save**，应用会自动重启（约 1 分钟）

> 改密码：改完 Save 即可；取消密码：删掉 `APP_PASSWORD` 那一行再 Save。
> 忘记密码：直接看 Secrets（它是明文存储的，只有你自己能看）。

### 3.2 密码功能是怎么生效的（写给要看懂原理的你）

密码读取优先级：**Streamlit Secrets > 系统环境变量 > 项目根目录 `.env`**。

```python
# app.py（已写好）
_sync_secrets_to_env()          # ① 把 Secrets 同步进环境变量，云端本地都能读到
↓
_get_access_password()          # ② 按优先级读密码；读不到 → 返回空 → 不启用
↓
check_password()                # ③ 为空直接放行；非空则渲染登录卡片
↓
if not check_password(): st.stop()   # ④ 未通过就停止渲染后面所有内容
```

关键设计：

- **可选开关**：三个来源都没配 `APP_PASSWORD` 时返回空字符串，函数直接 `return True`，本地开发完全无感
- **不重复打断**：验证通过写入 `st.session_state["auth_ok"]`，同一浏览器会话内不再重复输入
- **不落盘**：密码只在内存中比对，不写进任何文件
- **`st.stop()` 是官方推荐的拦截方式**，比 `return` 更彻底——未通过时连标题和侧边栏都不会渲染

### 3.3 本地先验证一遍（可选）

```powershell
# Windows PowerShell：临时设置环境变量后启动
$env:APP_PASSWORD="test123"
streamlit run app.py
```

- 浏览器打开 `http://localhost:8501`，应先看到"🔒 需要访问密码"
- 输错 → 提示"密码不正确，请重试"
- 输对 → 进入主界面，之后刷新不再要求输入
- 关掉该终端 → 环境变量消失，本地又恢复无密码状态

---

## 四、部署后验收清单

部署完成后逐项确认，全部打勾才算真正可用：

- [ ] 打开网址**先弹密码框**，输入正确密码才能进入
- [ ] 侧边栏显示 API Key 状态为「已配置」（不是红色警告）
- [ ] 上传一个 PDF → 能解析入库（首次含模型加载，约 1–2 分钟）
- [ ] 自动生成了「全文摘要」和 3 个推荐问题
- [ ] 点推荐问题提问 → 回答**逐字流式**输出
- [ ] 展开「📚 命中原文片段与相似度」能看到原文和百分比
- [ ] 换浏览器/无痕窗口打开 → 再次要求输入密码（说明密码确实生效）

---

## 五、常见问题与排障

| 现象 | 原因 | 解决办法 |
| --- | --- | --- |
| 部署日志卡在下载模型 | HuggingFace 网络慢 | 代码已内置 `hf-mirror` 镜像；仍失败则 Reboot 重试 |
| `ModuleNotFoundError` | 依赖没装全 | 确认 `requirements.txt` 在仓库根目录；改完点 Reboot |
| 应用显示「API Key 未配置」 | Secrets 没生效 | 检查 Secrets 的 TOML 拼写（键名必须是 `DEEPSEEK_API_KEY`），Save 后等重启完成 |
| 密码框不出现 | `APP_PASSWORD` 没配上 | 确认 Secrets 里没有多余空格/中文引号；本地可用 `st.secrets` 调试 |
| 输对密码仍进不去 | 浏览器缓存旧会话 | 用无痕窗口测试；或点应用菜单 → Clear cache |
| 容器重启后文档没了 | 云端无持久磁盘 | 已知限制，重启后需重新上传 PDF（见 README 云端注意事项表） |
| 内存超限被杀（OOM） | 免费版约 1GB | 一次不要上传过多/过大的 PDF；可改用 `bge-small` 已是最省方案 |
| 应用长时间无人访问后打不开 | 免费版会休眠 | 打开时会自动唤醒，等 1–2 分钟即可 |
| **一直转圈、几分钟进不去** | 云端构建/冷启动慢，多数是 `torch` 拉了 CUDA 全家桶 | 见下节「六、一直加载的定位与解决」 |
| 手机打不开但电脑能开 | 手机切后台/锁屏会断开 WebSocket | 保持前台、别锁屏；回到应用后手动刷新页面 |

---

## 六、一直加载 / 打不开的定位与解决

### 6.1 先看日志（30 秒就能定位）

应用页 → 右下角「管理应用」→ 打开 **Logs**，对照下表：

| 日志里看到 | 说明 | 怎么办 |
| --- | --- | --- |
| `Installing dependencies...` / 一堆 pip 下载进度 | **还在装依赖**，没构建完 | 等着，torch 是大头（见 6.2） |
| `Downloading BAAI/bge-small-zh-v1.5` | 正在下向量模型（约 100MB） | 等 1–3 分钟 |
| 反复出现 `App is starting` 后消失 | **容器被内存限制杀掉**（OOM） | 见 6.3 |
| 红色 Traceback | 代码/依赖报错 | 把报错发出来排查 |

### 6.2 头号原因：torch 默认装 CUDA 版（已修复）

`sentence-transformers` 依赖 `torch`。在 **Linux** 上 `pip install torch` 会连带拉 `nvidia-*` 一整套 CUDA 包，
**总体积数 GB**，而 Streamlit Cloud 免费版磁盘与构建时间都有限，于是表现就是"永远在启动"。

已在 `requirements.txt` 中修复：

```ini
--extra-index-url https://download.pytorch.org/whl/cpu
torch==2.14.1+cpu ; sys_platform == "linux"     # 云端只装 CPU 版，约 200MB
torch>=2.0 ; sys_platform != "linux"            # 本地 Windows/macOS 不受影响
```

> 改完依赖后必须让云端**重新构建**：Manage app → **Reboot**（必要，否则仍用旧依赖）。

### 6.3 内存接近上限时的减负手段

免费版约 1GB 内存，`torch + chromadb + BGE 模型` 同时加载比较吃紧：

- Advanced settings 里 Python 版本建议选 **3.11 或 3.12**
- 一次不要上传过多 PDF（向量常驻内存）
- 若日志显示 OOM 反复重启，可考虑改用更轻量的嵌入方案（如 ONNX 量化版 bge-small）

### 6.4 手机访问的两个坑

1. **切后台/锁屏会断开 WebSocket**：Streamlit 靠长连接通信，断开后页面会一直转圈，回到前台需**手动刷新**
2. **首次构建期间不要反复刷新**：每次刷新都会打断构建进度，建议先用电脑开着日志看进度

---

## 七、本次为部署所做的代码改动（两处，都在 `app.py`）

1. **`_sync_secrets_to_env()`** —— 云端适配。Streamlit Cloud 的 Secrets 不保证自动进入 `os.environ`，而 `qa.py` 是用 `os.getenv()` 读 Key 的。显式同步一次，保证「本地 `.env`」和「云端 Secrets」走同一套读取逻辑。
2. **`check_password()` / `_get_access_password()`** —— 访问密码。按上述优先级读密码，未配置则自动跳过；通过 `st.session_state` 记住登录状态，用 `st.stop()` 拦截未授权访问。

配套改动：`.gitignore` 增加 `.streamlit/secrets.toml`、`.pytest_cache/`；`.env.example` 增加 `APP_PASSWORD` 注释行。

---

## 八、安全提醒

- `.env` 已在 `.gitignore` 中，**永远不要**把它提交到任何仓库（尤其是公开仓库）
- 云端密钥只走 Streamlit Secrets，它加密存储、仅你可见、不会出现在代码里
- 若 Key 泄露：到 DeepSeek 控制台**立即吊销并重新生成**，然后更新 Secrets
- 私有仓库 + 密码是双重保护；若之后要开源，请把仓库设为 Public 前再确认一遍 `.env` 从未入库
