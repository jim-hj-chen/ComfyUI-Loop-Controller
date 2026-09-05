# ComfyUI-Loop-Controller（循环控制器）

一个用于 ComfyUI 的“基于序列排队的无人值守循环批处理”插件。  
核心思路是：**不用 Python `for` 一次性跑完，而是每轮结束后自动向 ComfyUI 再排一条队列任务**，从而获得更稳定、可中断、可追踪的循环执行体验。

---

## 功能特性

- 纯净序列排队：每次只执行当前一轮，尾节点自动触发下一轮排队。
- 隐式状态共享：`Loop Start` 写入内存状态，`Loop Trigger` 读取，无需拉长线。
- Fail-Fast：`List Item Extractor` 越界直接报错中断，避免错位数据继续流转。
- 跟随 ComfyUI 界面语言：节点名称、分类、输入输出与提示会随语言设置切换（英文 / 简体中文），不会在同一节点上并排显示中英。
- 进度实时反馈：触发节点面板按当前语言显示进度，例如 `Progress: 1 / 10` 或 `进度：1 / 10`。

---

## 目录结构

```text
comfyui-loop-controller/
├─ __init__.py
├─ nodes.py
├─ locales/
│  ├─ en/
│  │  ├─ main.json
│  │  └─ nodeDefs.json
│  └─ zh/
│     ├─ main.json
│     └─ nodeDefs.json
└─ web/
   └─ loop_controller.js
```

---

## 安装方式

1. 将本插件目录放入 ComfyUI 的自定义节点目录：
   - `ComfyUI/custom_nodes/comfyui-loop-controller`
2. 重启 ComfyUI。
3. 在节点分类中找到：
   - 英文界面：`Loop Controller`
   - 中文界面：`循环控制器`

界面语言由 ComfyUI 设置决定（Settings → Locale / 语言）。切换语言后刷新页面即可看到对应文案。

---

## 节点说明

### 1) Loop Start（中文：循环起始）

**作用**：循环时钟引擎，产出当前轮次序号。

**输入**
- `total` (`INT`)：总轮次，默认 `10`，范围 `1 ~ 100000`
- `start_index` (`INT`)：手动 Queue 时的起始序号，默认 `0`

**隐藏输入**
- `extra_pnginfo`：用于识别是否为自动循环请求（`is_auto_loop`）

**输出**
- `index` (`INT`)：当前轮次序号

**机制**
- 自动排队触发：读取当前 prompt 中的 `start_index`（由 `Loop Trigger` 在上一轮入队时改写）
- 手动点击 Queue：`current_index = start_index`
- 同时写入共享状态：`current_index`、`total`、`session_id`

---

### 2) List Item Extractor（中文：列表项提取器）

**作用**：按序号提取任意类型列表中的单个元素。

**输入**
- `index` (`INT`)：来自 `Loop Start` 的当前序号
- `list` (`*`)：任意类型输入（文本、列表、批量对象等）

**输出**
- `item` (`*`)：提取出的单个元素

**机制**
- 多行字符串会自动按 `\n` 拆分并去掉空行
- `list/tuple` 保持列表语义
- 非列表输入会转为单元素列表兜底

**Fail-Fast**
- 空列表：抛 `ValueError`
- 索引越界：抛 `IndexError`
  - 示例：`❌ 索引越界！要求获取第 X 个，但列表总共只有 Y 个`

---

### 3) Loop Trigger（中文：循环触发器）

**作用**：流程末端控制节点，判断是否继续并自动排队下一轮。

**输入**
- `any` (`*`)：用于控制执行顺序，通常连接保存节点的输出

**隐藏输入**
- `prompt`：当前完整工作流数据
- `client_id`：当前客户端 ID

**输出**
- 无数据输出（该节点为 `OUTPUT_NODE = True`）

**机制**
- 读取共享状态中的 `current_index` 与 `total`
- 若 `current_index + 1 < total`：
  - 构造新的请求
  - 改写下一条 prompt 中 `Loop Start.start_index = current_index + 1`
  - 注入 `extra_data.extra_pnginfo.loop_controller`：
    - `is_auto_loop: true`
    - `next_index: current_index + 1`
    - `total: total`
    - `session_id: <current loop session>`
  - POST 到 `http://127.0.0.1:8188/prompt` 自动继续排队
- 同一轮次的重复触发会被防重（不会重复注入下一轮）
- 后端返回 `ui.text` 进度文本，前端在中文界面自动本地化显示：
  - 英文进行中：`Progress: x / total`
  - 中文进行中：`进度：x / total`
  - 完成后分别显示 `✅ Finished` / `✅ 已完成`

---

## 推荐连线方式

典型链路如下（示例）：

1. `Loop Start.index` -> `List Item Extractor.index`
2. 你的数据源（如多行文本） -> `List Item Extractor.list`
3. `List Item Extractor.item` -> 下游生成节点
4. 最终保存/输出节点 -> `Loop Trigger.any`

> 建议将 `Loop Trigger` 放在工作流最末端，确保每轮真正完成后再触发下一轮。

---

## 常见问题

### Q1: 为什么没有继续自动排队？

- 检查 `Loop Trigger` 是否在末端并且实际被执行。
- 检查 ComfyUI API 地址是否可用（默认 `127.0.0.1:8188`）。
- 查看是否出现了上游异常（例如提取器越界）。

### Q2: 为什么会越界报错？

- 这是设计行为（Fail-Fast）。
- 例如你设置 `total=20`，但实际列表仅 10 项，当 index 到 10 后就会报错并停止，防止错位生成。

### Q3: 手动点击 Queue 后 index 为什么重置？

- 手动 Queue 被视为“新一轮任务入口”，`Loop Start` 会将 `current_index` 设为 `start_index`。

### Q4: 为什么现在不会在一个任务里“连跳 index”了？

- 旧逻辑按“执行次数”自增 index，若同一任务里节点被重复执行会出现连跳。
- 新逻辑按“队列任务”推进：下一轮 index 由 `Loop Trigger` 在入队前直接改写下一条 prompt 的 `Loop Start.start_index`，并附带 `next_index` 元数据用于观测，不再依赖自动模式里的无条件 `+1`。
- 因此语义和 Sequential-Batcher 一致：一轮一任务，而不是单任务内跑完整批。

---

## 设计原则

- 不使用单次长循环，避免中间崩溃导致整批失控。
- 状态显式可控、执行顺序可控、错误快速暴露。
- 优先稳定性与可维护性，而非静默容错。

---

## 许可

可按你的项目需要补充许可证（如 MIT）。
