import { app } from "../../scripts/app.js";

const NODE_UI = {
  "Loop Start": {
    title: "Loop Start / 循环起始",
    size: [360, 210],
    color: "#295f8f",
    bgcolor: "#1f3f5d",
    widgetLabels: {
      total: "total / 总次数",
      start_index: "start_index / 起始序号",
    },
  },
  "List Item Extractor": {
    title: "List Item Extractor / 列表项提取器",
    size: [380, 210],
    color: "#2f7f5f",
    bgcolor: "#1f4f3c",
    widgetLabels: {
      index: "index / 序号",
    },
    inputLabels: {
      list: "list / 列表",
    },
  },
  "Loop Trigger": {
    title: "Loop Trigger / 循环触发器",
    size: [360, 180],
    color: "#875f26",
    bgcolor: "#5f3f18",
    inputLabels: {
      any: "any / 执行依赖",
    },
  },
};

function applyBilingualUi(node, cfg) {
  node.title = cfg.title;
  node.size = cfg.size.slice();
  node.color = cfg.color;
  node.bgcolor = cfg.bgcolor;

  if (cfg.widgetLabels && Array.isArray(node.widgets)) {
    for (const w of node.widgets) {
      const label = cfg.widgetLabels[w.name];
      if (label) {
        w.label = label;
      }
    }
  }

  if (cfg.inputLabels && Array.isArray(node.inputs)) {
    for (const input of node.inputs) {
      const label = cfg.inputLabels[input.name];
      if (label) {
        input.label = label;
      }
    }
  }
}

app.registerExtension({
  name: "comfyui.loop.controller.bilingual_ui",
  beforeRegisterNodeDef(nodeType, nodeData) {
    const cfg = NODE_UI[nodeData.name];
    if (!cfg) return;

    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const result = onNodeCreated?.apply(this, arguments);
      applyBilingualUi(this, cfg);
      return result;
    };
  },
});
