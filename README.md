# AUTOSAR ARXML Workbench

面向 AUTOSAR 通信矩阵分析的轻量级 ARXML 解析与检索工具。它可以从本地 ARXML 文件中提取 CAN Frame、I-PDU、Signal、Signal Group、通道、方向和 Message ID 等结构化信息，并导出便于检索和评审的 JSON、TXT、Markdown 报告。

> 注意：本仓库只包含工具代码，不包含任何 `.arxml` 文件或由项目 ARXML 生成的业务数据。

## 功能

- 解析 AUTOSAR ARXML 中的 CAN Frame、PDU、Signal、Signal Group 关系
- 识别 Frame/PDU/Signal 的收发方向、CAN 通道和 Message ID
- 支持 Container I-PDU 成员展开
- 导出三类结果：
  - `*.parsed.json`：结构化数据，适合二次处理
  - `*.search.txt`：紧凑检索索引，适合全文搜索
  - `*.report.md`：可读报告，适合评审
- 提供单文件 Web Viewer，可在浏览器本地打开、拖拽 ARXML 进行可视化检索
- 无需联网处理 ARXML，数据默认留在本机浏览器或本地脚本环境

## 仓库内容

```text
.
├── parse_arxml_export.py      # 通用 ARXML 解析与报告导出脚本
├── build_rx_frame_map.py      # 按指定 CAN 通道生成 RX Frame/PDU 映射
├── arxml_viewer.html          # 本地 Web 可视化检索页面
├── assets/                    # Web 页面静态资源
├── vercel.json                # 可选：静态页面部署配置
└── .gitignore                 # 默认排除 ARXML 和生成数据
```

## 使用方式

### 1. 命令行解析

```bash
python3 parse_arxml_export.py -i /path/to/example.arxml -o outputs
```

输出：

```text
outputs/example.parsed.json
outputs/example.search.txt
outputs/example.report.md
```

### 2. 生成指定通道的 RX Frame 映射

```bash
python3 build_rx_frame_map.py \
  -i /path/to/example.arxml \
  -o outputs/rx_frame_map.json \
  --channels CANFD1,CANFD2
```

### 3. 浏览器本地查看

直接用浏览器打开 `arxml_viewer.html`，选择或拖入本地 `.arxml` / `.xml` 文件即可。

Web Viewer 的设计目标是“本地分析”：ARXML 文件不会随仓库上传，也不需要提交到服务器才能解析。

## 隐私与发布说明

ARXML 往往包含车型、域控、网络拓扑、信号命名、PDU/Frame 映射等敏感工程信息。因此本仓库默认：

- 不上传 `.arxml` / `.xml` 文件
- 不上传由 ARXML 导出的 `*.json`、`*.txt`、`*.md` 报告
- 不在导出结果中写入本机绝对路径，只保留源文件名
- 不依赖示例业务数据即可运行工具代码

如果需要提交演示数据，建议使用脱敏后的最小样例，并确保不包含真实项目名称、车型代号、信号定义或网络拓扑。

## 运行环境

- Python 3.10+
- 标准库实现，无额外 Python 依赖
- Web Viewer 支持现代浏览器

## License

MIT License
