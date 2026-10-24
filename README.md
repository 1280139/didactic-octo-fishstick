# 研究生管理系统 Demo

老师派任务、学生写周报和打卡的最小可演示系统。Flask + SQLite + Jinja2，单进程、零前端构建。

## 本地运行

```bash
pip install -r requirements.txt
python app.py
```

打开 http://127.0.0.1:8000

| 账号 | 密码 | 身份 |
|---|---|---|
| `t001` | `123456` | 张老师 |
| `s001` | `123456` | 张三（状态正常） |
| `s002` | `123456` | 李四（本周已交周报） |
| `s003` | `123456` | 王五（问题学生，演示重点） |

数据库 `demo.db` 首次启动自动创建并灌入种子数据。老师登录后页脚有「重置演示数据」，改乱了点一下即可恢复。

## 部署到 Render（免费）

1. 把本目录推到 GitHub 仓库
2. 打开 [render.com](https://render.com) → **New → Web Service** → 选择该仓库
3. 自动读取 `render.yaml`，点 **Create**
4. 等 2-3 分钟，得到 `https://xxx.onrender.com`

免费档特性：15 分钟无访问会休眠（下次打开等约 30 秒），重启后数据库重置 —— 对 demo 是优点，数据永远干净。

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `PORT` | `8000` | 监听端口，云平台自动注入 |
| `SECRET_KEY` | 内置占位值 | **公网部署必须改**，否则可伪造登录 cookie |
| `DATA_DIR` | 项目目录 | 数据库存放目录，挂载持久磁盘时指向它 |
| `HTTPS_ONLY` | 未设置 | 设为 `1` 时 cookie 只走 HTTPS |

生成密钥：

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

## 其他运行方式

**Docker：**

```bash
docker build -t grad-demo .
docker run -p 8000:8000 -v grad-data:/data -e SECRET_KEY=换成随机值 grad-demo
```

**局域网访问（同一 WiFi 的手机）：**

```bash
python app.py
# 手机打开 http://<你电脑的IP>:8000
```

Windows 若被防火墙拦截：

```powershell
New-NetFirewallRule -DisplayName "Demo 8000" -Direction Inbound -LocalPort 8000 -Protocol TCP -Action Allow
```

## 健康检查

`GET /healthz` → `{"status":"ok","db":"demo.db"}`，云平台用它判断实例是否存活。

## 测试

```bash
python app.py          # 另开一个终端
python smoke_test.py   # 25 项 HTTP 冒烟测试
```

覆盖：登录跳转、越权拦截、打卡幂等、周报覆盖、任务状态流转、名单联动更新、静态资源与 404。

## 目录结构

```
demo/
├── app.py              # 后端全部逻辑
├── smoke_test.py       # 冒烟测试
├── requirements.txt
├── render.yaml         # Render 部署配置
├── runtime.txt         # Python 版本
├── Dockerfile
├── templates/          # 7 个 Jinja2 模板
└── static/style.css    # 手写 CSS，无依赖
```
