import { app } from "/scripts/app.js";
import { ComfyWidgets } from "/scripts/widgets.js";

const NODE_UI = {
  "Loop Start": {
    size: [320, 180],
    color: "#295f8f",
    bgcolor: "#1f3f5d",
  },
  "List Item Extractor": {
    size: [340, 180],
    color: "#2f7f5f",
    bgcolor: "#1f4f3c",
  },
  "Loop Trigger": {
    size: [340, 200],
    color: "#875f26",
    bgcolor: "#5f3f18",
  },
};

function getComfyLocale() {
  try {
    const fromSettings = app.ui?.settings?.getSettingValue?.("Comfy.Locale");
    if (fromSettings) {
      return String(fromSettings);
    }
  } catch {
    // Fall through to localStorage / navigator.
  }

  try {
    const stored = localStorage.getItem("Comfy.Settings.Comfy.Locale");
    if (stored) {
      return String(JSON.parse(stored));
    }
  } catch {
    // Ignore malformed locale storage.
  }

  return navigator.language || "en";
}

function isChineseLocale() {
  return getComfyLocale().toLowerCase().startsWith("zh");
}

function getProgressTextFromMessage(message) {
  if (!message?.text || !Array.isArray(message.text) || message.text.length === 0) {
    return "";
  }
  const text = message.text[0];
  return typeof text === "string" ? text : String(text ?? "");
}

function localizeProgressText(text) {
  if (!isChineseLocale()) return text;

  const runningMatch = text.match(/^Progress:\s*(\d+)\s*\/\s*(\d+)$/);
  if (runningMatch) {
    return `进度：${runningMatch[1]} / ${runningMatch[2]}`;
  }

  const finishedMatch = text.match(/^✅\s*Finished:\s*(\d+)\s*\/\s*(\d+)$/);
  if (finishedMatch) {
    return `✅ 已完成：${finishedMatch[1]} / ${finishedMatch[2]}`;
  }

  return text;
}

function ensureProgressWidget(node) {
  if (node._loopProgressWidget) return node._loopProgressWidget;

  const widgetPack = ComfyWidgets.STRING(
    node,
    "progress",
    ["STRING", { multiline: true, default: "" }],
    app
  );
  const widget = widgetPack.widget;
  widget.label = isChineseLocale() ? "进度" : "progress";
  widget.inputEl.readOnly = true;
  widget.inputEl.style.opacity = "0.85";
  widget.inputEl.style.fontSize = "12px";
  widget.inputEl.style.minHeight = "56px";
  node._loopProgressWidget = widget;
  node.onResize?.(node.size);
  return widget;
}

function applyProgress(node, message) {
  const progressText = getProgressTextFromMessage(message);
  if (!progressText) return;
  const widget = ensureProgressWidget(node);
  widget.value = localizeProgressText(progressText);
  node.onResize?.(node.size);
}

app.registerExtension({
  name: "comfyui.loop.controller.ui",
  beforeRegisterNodeDef(nodeType, nodeData) {
    const cfg = NODE_UI[nodeData.name];
    if (!cfg) return;

    const onNodeCreated = nodeType.prototype.onNodeCreated;
    nodeType.prototype.onNodeCreated = function () {
      const result = onNodeCreated?.apply(this, arguments);
      this.size = cfg.size.slice();
      this.color = cfg.color;
      this.bgcolor = cfg.bgcolor;

      if (nodeData.name === "Loop Trigger") {
        ensureProgressWidget(this);
      }

      return result;
    };

    if (nodeData.name === "Loop Trigger") {
      const onExecuted = nodeType.prototype.onExecuted;
      nodeType.prototype.onExecuted = function (message) {
        onExecuted?.apply(this, arguments);
        applyProgress(this, message);
      };
    }
  },
});
