import { app } from "/scripts/app.js";
import { api } from "/scripts/api.js";

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
    size: [340, 220],
    color: "#875f26",
    bgcolor: "#5f3f18",
  },
};

const PROGRESS_WIDGET_HEIGHT = 72;

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

function parseProgressFromText(text) {
  if (!text) return null;

  const runningMatch = text.match(/Progress:\s*(\d+)\s*\/\s*(\d+)/i);
  if (runningMatch) {
    return {
      completed: Number(runningMatch[1]),
      total: Number(runningMatch[2]),
      done: false,
    };
  }

  const finishedMatch = text.match(/Finished:\s*(\d+)\s*\/\s*(\d+)/i);
  if (finishedMatch) {
    return {
      completed: Number(finishedMatch[1]),
      total: Number(finishedMatch[2]),
      done: true,
    };
  }

  const localizedMatch = text.match(/(\d+)\s*\/\s*(\d+)/);
  if (localizedMatch) {
    return {
      completed: Number(localizedMatch[1]),
      total: Number(localizedMatch[2]),
      done: /finished|已完成/i.test(text),
    };
  }

  return null;
}

function parseProgressFromMessage(message) {
  const payload = message?.progress?.[0];
  if (payload && typeof payload === "object") {
    const completed = Number(payload.completed);
    const total = Number(payload.total);
    if (Number.isFinite(completed) && Number.isFinite(total) && total > 0) {
      return {
        completed,
        total,
        done: Boolean(payload.done),
      };
    }
  }

  return parseProgressFromText(getProgressTextFromMessage(message));
}

function progressLabels() {
  if (isChineseLocale()) {
    return {
      idle: "等待开始",
      running: "进度",
      done: "已完成",
      idleCount: "—",
    };
  }
  return {
    idle: "Waiting",
    running: "Progress",
    done: "Finished",
    idleCount: "—",
  };
}

function roundRect(ctx, x, y, w, h, r) {
  const radius = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + radius, y);
  ctx.arcTo(x + w, y, x + w, y + h, radius);
  ctx.arcTo(x + w, y + h, x, y + h, radius);
  ctx.arcTo(x, y + h, x, y, radius);
  ctx.arcTo(x, y, x + w, y, radius);
  ctx.closePath();
}

function drawCheckmark(ctx, x, y, size, color) {
  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.beginPath();
  ctx.moveTo(x, y + size * 0.55);
  ctx.lineTo(x + size * 0.35, y + size * 0.9);
  ctx.lineTo(x + size, y + size * 0.15);
  ctx.stroke();
  ctx.restore();
}

function drawProgressWidget(ctx, node, widgetWidth, y) {
  const margin = 12;
  const x = margin;
  const width = widgetWidth - margin * 2;
  const height = PROGRESS_WIDGET_HEIGHT;
  const state = node._loopProgressState;
  const labels = progressLabels();
  const done = Boolean(state?.done);
  const hasProgress = Boolean(state && state.total > 0);
  const ratio = hasProgress
    ? Math.max(0, Math.min(1, state.completed / state.total))
    : 0;
  const percent = hasProgress ? Math.round(ratio * 100) : 0;

  ctx.save();
  roundRect(ctx, x, y, width, height, 8);
  ctx.fillStyle = "rgba(0, 0, 0, 0.28)";
  ctx.fill();
  ctx.strokeStyle = "rgba(255, 214, 150, 0.18)";
  ctx.lineWidth = 1;
  ctx.stroke();

  const statusColor = done ? "#8fd4a8" : hasProgress ? "#f0c27a" : "#c4b49a";
  ctx.fillStyle = statusColor;
  ctx.font = "11px sans-serif";
  ctx.textAlign = "left";
  ctx.textBaseline = "top";
  const statusText = done ? labels.done : hasProgress ? labels.running : labels.idle;
  const statusX = x + 12;
  ctx.fillText(statusText, statusX, y + 10);
  if (done) {
    const statusWidth = ctx.measureText(statusText).width;
    drawCheckmark(ctx, statusX + statusWidth + 8, y + 10, 11, "#8fd4a8");
  }

  ctx.fillStyle = "#f7f1e6";
  ctx.font = "bold 18px sans-serif";
  ctx.textAlign = "right";
  ctx.fillText(hasProgress ? `${percent}%` : labels.idleCount, x + width - 12, y + 6);

  ctx.fillStyle = "rgba(247, 241, 230, 0.72)";
  ctx.font = "12px sans-serif";
  ctx.textAlign = "left";
  const countText = hasProgress
    ? `${state.completed} / ${state.total}`
    : labels.idleCount;
  ctx.fillText(countText, x + 12, y + 28);

  const barX = x + 12;
  const barY = y + 48;
  const barW = width - 24;
  const barH = 10;

  roundRect(ctx, barX, barY, barW, barH, 5);
  ctx.fillStyle = "rgba(20, 12, 6, 0.55)";
  ctx.fill();

  if (ratio > 0) {
    const fillW = Math.max(barH, barW * ratio);
    ctx.save();
    roundRect(ctx, barX, barY, fillW, barH, 5);
    const gradient = ctx.createLinearGradient(barX, barY, barX + fillW, barY);
    if (done) {
      gradient.addColorStop(0, "#3f8f62");
      gradient.addColorStop(1, "#7fd39a");
    } else {
      gradient.addColorStop(0, "#875f26");
      gradient.addColorStop(1, "#e8b86a");
    }
    ctx.fillStyle = gradient;
    ctx.fill();
    ctx.restore();
  }

  ctx.restore();
}

function ensureProgressWidget(node) {
  if (node._loopProgressWidget) return node._loopProgressWidget;

  const widget = node.addCustomWidget({
    type: "loop_progress",
    name: "",
    options: { hideOnGraph: false },
    y: 0,
    serialize: false,
    draw(ctx, drawNode, widgetWidth, y) {
      drawProgressWidget(ctx, drawNode, widgetWidth, y);
    },
    computeSize() {
      return [node.size?.[0] || 340, PROGRESS_WIDGET_HEIGHT];
    },
  });

  node._loopProgressWidget = widget;
  node._loopProgressState = null;
  node.onResize?.(node.size);
  return widget;
}

function applyProgress(node, message) {
  const parsed = parseProgressFromMessage(message);
  if (!parsed) return;
  const widget = ensureProgressWidget(node);
  node._loopProgressState = parsed;
  widget.value = parsed;
  node.setDirtyCanvas?.(true, true);
  node.onResize?.(node.size);
}

function getGraphNodeById(nodeId) {
  if (nodeId == null || !app.graph) return null;
  return (
    app.graph.getNodeById?.(nodeId) ||
    app.graph.getNodeById?.(Number(nodeId)) ||
    null
  );
}

function isLoopTriggerNode(node) {
  const className = node?.comfyClass || node?.type;
  return className === "Loop Trigger";
}

function applyExecutedOutput(detail) {
  const node = getGraphNodeById(detail?.display_node ?? detail?.node);
  if (!isLoopTriggerNode(node)) return;
  applyProgress(node, detail.output || detail);
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
  setup() {
    api.addEventListener("executed", (event) => {
      applyExecutedOutput(event.detail);
    });
  },
});
