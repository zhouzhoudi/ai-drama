# AI 短剧自动生成系统（ai-drama）

基于 AI 的短视频自动创作平台，从剧本到成片一键完成。

## 🎯 功能特性

- **智能剧本生成** - 输入需求，自动生成完整剧本和分镜
- **角色一致性** - 使用 MiniMax 生成角色参考图，保持人物一致
- **视频自动生成** - 对接可灵 API，批量生成分镜视频
- **配音合成** - 使用 MiniMax TTS v2 生成自然配音
- **音视频合并** - FFmpeg 自动合并，输出最终成片
- **多端触发** - 支持飞书、前端页面、定时任务多种触发方式

## 📁 项目结构

```
ai-drama-system/
├── backend/                    # 后端服务
│   ├── main.py                # FastAPI 主入口
│   └── services/              # 服务模块
│       ├── script_generator.py   # 剧本生成
│       ├── character_generator.py # 角色图生成
│       ├── video_generator.py    # 视频生成
│       ├── audio_generator.py    # 音频生成
│       └── merger.py            # 音视频合并
├── frontend/                   # 前端页面
│   ├── src/
│   │   ├── App.js            # React 主组件
│   │   └── App.css           # 样式文件
│   └── public/
├── data/
│   ├── scripts/              # 剧本数据
│   │   └── {script_id}/
│   │       ├── script.json
│   │       ├── characters/
│   │       └── shots/
│   └── outputs/             # 最终成片
├── config/
│   └── openclaw_workflow_rules.json  # 工作流规则
└── README.md
```

## 🚀 快速开始

### 1. 安装依赖

```bash
# 后端依赖
cd backend
pip install fastapi uvicorn requests

# 前端依赖
cd frontend
npm install
```

### 2. 配置 API Key

在 `~/.hermes/.env` 中添加：

```
MINIMAX_CN_API_KEY=your_minimax_api_key
KLING_API_KEY=your_kling_api_key
```

### 3. 启动服务

```bash
# 终端 1: 启动后端
cd backend
python main.py

# 终端 2: 启动前端
cd frontend
npm start
```

### 4. 访问页面

打开浏览器访问: http://localhost:3000

## 📡 API 接口

| 接口 | 方法 | 描述 |
|------|------|------|
| `/` | GET | 服务状态 |
| `/api/scripts` | POST | 创建新剧本 |
| `/api/scripts/{id}` | GET | 获取剧本详情 |
| `/api/scripts/{id}/status` | GET | 获取生成状态 |
| `/api/scripts/{id}/characters/{char_id}/generate-image` | POST | 生成角色图 |
| `/api/scripts/{id}/shots/batch-generate` | POST | 批量生成分镜 |
| `/api/scripts/{id}/merge` | POST | 合并最终视频 |

## 🎬 使用流程

### 通过前端页面

1. 打开 http://localhost:3000
2. 填写短剧标题、题材、风格、剧情描述
3. 添加角色信息
4. 点击"开始生成短剧"
5. 等待生成完成，查看最终成片

### 通过飞书

发送消息给机器人：
```
/短剧 都市爱情风格，3分钟，女主小美是职场新人，男主小明是公司高管
```

## 📊 数据结构

### 剧本 JSON

```json
{
  "script_id": "uuid",
  "title": "标题",
  "genre": "都市爱情",
  "style": "甜蜜治愈",
  "characters": [...],
  "scenes": [...]
}
```

详见: `data/scripts/schemas/script_schema.json`

## 🔧 工作流配置

OpenClaw 工作流规则定义在: `config/openclaw_workflow_rules.json`

可导入到支持 JSON 工作流规则的系统中使用。

## 📝 开发指南

### 添加新的生成器

1. 在 `backend/services/` 创建新的 generator 文件
2. 实现核心生成方法
3. 在 `main.py` 中添加对应的 API 路由

### 自定义工作流

编辑 `config/openclaw_workflow_rules.json` 修改工作流步骤。

## ⚠️ 注意事项

- 确保 MiniMax API 有足够的余额
- 可灵视频生成需要排队，等待时间可能较长
- 音视频合并需要安装 FFmpeg

## 📄 License

MIT
