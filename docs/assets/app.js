const state = {
  recording: null, preview: [], cadence: [], metrics: [], metadata: null,
  analysis: null, muscles: [], reportAvailability: new Map(),
  start: 0, window: 600, syncing: false, activeView: "assessment",
};
const $ = (selector) => document.querySelector(selector);
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
let colors = [];
const chartColors = Object.freeze({
  leftResistance: "#2563eb",
  rightResistance: "#e8590c",
  leftEmg: "#00856a",
  rightEmg: "#7c3aed",
});

function parseCsv(text) {
  const [header, ...lines] = text.trim().split(/\r?\n/).filter(Boolean);
  const fields = header.split(",");
  return lines.map((line) => Object.fromEntries(fields.map((field, index) => {
    const value = line.split(",")[index];
    const numeric = Number(value);
    return [field, value === "" ? null : Number.isNaN(numeric) ? value : numeric];
  })));
}

async function fetchText(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`無法載入 ${url}（HTTP ${response.status}）`);
  return response.text();
}

async function fetchJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`無法載入 ${url}（HTTP ${response.status}）`);
  return response.json();
}

function percentile(values, fraction) {
  const finite = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (!finite.length) return 1;
  const index = Math.min(finite.length - 1, Math.max(0, Math.round((finite.length - 1) * fraction)));
  return finite[index] || 1;
}

function elapsed(row) { return row.time_s - state.preview[0].time_s; }
function range() { return [state.start, Math.min(state.start + state.window, state.duration)]; }
function labelFor(channel) { return state.muscles.find((item) => item.channel === channel)?.label || channel; }
function timeLabel(seconds) {
  const wholeSeconds = Math.max(0, Math.floor(seconds));
  return `${Math.floor(wholeSeconds / 60)}:${String(wholeSeconds % 60).padStart(2, "0")}`;
}

function baseLayout() {
  const foreground = css("--color-text");
  const rule = css("--color-rule");
  return {
    paper_bgcolor: css("--color-surface-raised"), plot_bgcolor: css("--color-surface-raised"), font: { family: css("--font-body"), color: foreground },
    margin: { l: 58, r: 28, t: 22, b: 56 }, hovermode: "x unified", dragmode: "pan",
    xaxis: { title: "對齊後經過時間（秒）", range: range(), gridcolor: rule, zerolinecolor: rule },
    yaxis: { gridcolor: rule, zerolinecolor: rule },
    legend: { orientation: "h", y: -0.24, x: 0, font: { size: 11 }, groupclick: "toggleitem" },
    hoverlabel: { bgcolor: css("--color-ink"), bordercolor: css("--color-ink"), font: { color: css("--color-accent-ink"), family: css("--font-body") } },
  };
}

function makeEmgCharts() {
  const x = state.preview.map(elapsed);
  const host = $("#emg-charts");
  host.replaceChildren(...state.muscles.map(({ channel, label }, index) => {
    const metric = state.metrics.find((entry) => entry.channel === channel);
    const card = document.createElement("article");
    const id = `emg-chart-${index}`;
    card.className = "muscle-chart-card";
    const heading = document.createElement("div");
    const title = document.createElement("h3");
    const score = document.createElement("p");
    const chart = document.createElement("div");
    heading.className = "muscle-chart-heading";
    title.textContent = label;
    score.textContent = `週期一致度 ${metric.cycle_consistency.toFixed(3)}`;
    chart.id = id;
    chart.className = "chart muscle-chart";
    chart.dataset.muscleChart = "";
    chart.setAttribute("aria-label", `${label} RMS 活化曲線`);
    heading.append(title, score);
    card.append(heading, chart);
    return card;
  }));
  return Promise.all(state.muscles.map(({ channel, label }, index) => {
    const layout = { ...baseLayout(), showlegend: false, yaxis: { ...baseLayout().yaxis, title: "RMS（µV）" }, height: 220 };
    const trace = {
      x, y: state.preview.map((row) => Number.isFinite(row[channel]) ? row[channel] * 1e6 : null),
      name: label, type: "scatter", mode: "lines", line: { color: colors[index], width: 1.4 },
      hovertemplate: `%{x:.2f} 秒<br>${label}：%{y:.2f} µV<extra></extra>`,
    };
    return Plotly.react(`emg-chart-${index}`, [trace], layout, { responsive: true, displaylogo: false, scrollZoom: true });
  }));
}

function makeResistanceChart() {
  const x = state.preview.map(elapsed);
  const leftColor = chartColors.leftResistance;
  const rightColor = chartColors.rightResistance;
  const traces = [
    ["crankLeft", "左側阻力", leftColor], ["CrankRight", "右側阻力", rightColor],
  ].map(([channel, label, color]) => ({ x, y: state.preview.map((row) => row[channel]), name: label, type: "scatter", mode: "lines", line: { color, width: 2 }, hovertemplate: `%{x:.2f} 秒<br>${label}：%{y:.2f}<extra></extra>` }));
  const layout = { ...baseLayout(), yaxis: { ...baseLayout().yaxis, title: "Resistance 原始單位" }, height: 280 };
  return Plotly.react("resistance-chart", traces, layout, { responsive: true, displaylogo: false, scrollZoom: true });
}

function makeCadenceChart() {
  const origin = state.preview[0].time_s;
  const leftColor = chartColors.leftResistance;
  const rightColor = chartColors.rightResistance;
  const traces = ["Left", "Right"].map((side, index) => {
    const rows = state.cadence.filter((row) => row.side === side && Number.isFinite(row.strokes_per_min));
    const label = side === "Left" ? "左腳" : "右腳";
    return { x: rows.map((row) => row.time_s - origin), y: rows.map((row) => row.strokes_per_min), name: label, type: "scatter", mode: "lines", line: { color: index ? rightColor : leftColor, width: 2, shape: "spline", smoothing: 0.7 }, hovertemplate: `%{x:.1f} 秒<br>${label}：%{y:.1f} 次/分<extra></extra>` };
  });
  const layout = { ...baseLayout(), yaxis: { ...baseLayout().yaxis, title: "同側踩踏次數／分鐘" }, height: 250 };
  return Plotly.react("cadence-chart", traces, layout, { responsive: true, displaylogo: false, scrollZoom: true });
}

function meanNormalizedEnvelope(side) {
  const channels = state.muscles
    .filter(({ channel }) => channel.startsWith(`${side} `))
    .map(({ channel }) => channel);
  const scales = new Map(channels.map((channel) => [channel, percentile(
    state.preview.map((row) => Number(row[channel]) * 1e6), 0.9
  )]));
  return state.preview.map((row) => {
    const values = channels.map((channel) => Number(row[channel]) * 1e6 / scales.get(channel)).filter(Number.isFinite);
    return values.length ? values.reduce((total, value) => total + value, 0) / values.length * 100 : null;
  });
}

function makeSideSyncChart(side, chartId, emgColor, resistanceColor) {
  const x = state.preview.map(elapsed);
  const traces = [
    { y: meanNormalizedEnvelope(side), name: `${side === "L" ? "左" : "右"}側 EMG 彙整包絡`, color: emgColor, axis: "y" },
    { y: state.preview.map((row) => row[side === "L" ? "crankLeft" : "CrankRight"]), name: `${side === "L" ? "左" : "右"}側阻力`, color: resistanceColor, axis: "y2" },
  ].map(({ y, name, color, axis }) => ({
    x, y, name, yaxis: axis, type: "scatter", mode: "lines",
    line: { color, width: axis === "y" ? 2.2 : 1.8 },
    hovertemplate: `%{x:.2f} 秒<br>${name}：%{y:.2f}<extra></extra>`,
  }));
  const layout = {
    ...baseLayout(), height: 260,
    yaxis: { ...baseLayout().yaxis, title: "EMG 彙整包絡（各肌肉 P90 = 100%）", rangemode: "tozero" },
    yaxis2: { title: "Resistance 原始單位", overlaying: "y", side: "right", gridcolor: css("--color-transparent"), zerolinecolor: css("--color-transparent") },
  };
  return Plotly.react(chartId, traces, layout, { responsive: true, displaylogo: false, scrollZoom: true });
}

function makeSyncCharts() {
  return Promise.all([
    makeSideSyncChart("L", "sync-left-chart", chartColors.leftEmg, chartColors.leftResistance),
    makeSideSyncChart("R", "sync-right-chart", chartColors.rightEmg, chartColors.rightResistance),
  ]);
}

function renderSummary() {
  const assessment = state.analysis.subject_assessment;
  const focus = state.analysis.activation_consistency.priority_review;
  $("#focus-list").replaceChildren(...focus.map((item, index) => {
    const entry = document.createElement("li");
    const rank = document.createElement("span");
    const muscle = document.createElement("span");
    const score = document.createElement("span");
    rank.className = "focus-rank";
    muscle.className = "focus-muscle";
    score.className = "focus-score";
    rank.textContent = String(index + 1).padStart(2, "0");
    muscle.textContent = item.muscle;
    score.textContent = item.cycle_consistency.toFixed(3);
    entry.append(rank, muscle, score);
    return entry;
  }));
  $("#summary-text").textContent = assessment.summary;
}

function renderJointAnalysis() {
  const finding = state.analysis.subject_assessment.findings
    .find((item) => item.key === "load_relationship");
  $("#joint-analysis").textContent = finding?.text || "本次資料不足，未產生肌電與阻力共同變化的程式判讀。";
}

function appendInlineMarkdown(element, source) {
  const pattern = /(\*\*|__)(.+?)\1|`([^`]+)`|\*([^*\n]+)\*|_([^_\n]+)_/g;
  let cursor = 0;
  for (const match of source.matchAll(pattern)) {
    if (match.index > cursor) element.append(document.createTextNode(source.slice(cursor, match.index)));
    const tag = match[1] ? "strong" : match[3] ? "code" : "em";
    const inline = document.createElement(tag);
    inline.textContent = match[2] || match[3] || match[4] || match[5];
    element.append(inline);
    cursor = match.index + match[0].length;
  }
  if (cursor < source.length) element.append(document.createTextNode(source.slice(cursor)));
}

function renderNarrativeMarkdown(host, markdown) {
  const fragment = document.createDocumentFragment();
  let list = null;
  markdown.split(/\r?\n/).forEach((rawLine) => {
    const line = rawLine.trim();
    if (!line) { list = null; return; }
    const heading = line.match(/^#{1,4}\s+(.+)$/);
    if (heading) {
      list = null;
      const element = document.createElement("h3");
      appendInlineMarkdown(element, heading[1]);
      fragment.append(element);
      return;
    }
    const bullet = line.match(/^[-*+]\s+(.+)$/);
    const ordinal = line.match(/^\d+[.)]\s+(.+)$/);
    if (bullet || ordinal) {
      const type = ordinal ? "ol" : "ul";
      if (!list || list.tagName.toLowerCase() !== type) {
        list = document.createElement(type);
        fragment.append(list);
      }
      const item = document.createElement("li");
      appendInlineMarkdown(item, (bullet || ordinal)[1]);
      list.append(item);
      return;
    }
    if (line.startsWith("> ")) {
      list = null;
      const quote = document.createElement("blockquote");
      appendInlineMarkdown(quote, line.slice(2));
      fragment.append(quote);
      return;
    }
    list = null;
    const paragraph = document.createElement("p");
    appendInlineMarkdown(paragraph, line);
    fragment.append(paragraph);
  });
  host.replaceChildren(fragment);
}

function computedNarrativeMarkdown() {
  const assessment = state.analysis?.subject_assessment;
  if (!assessment) return "### 程式重點整理\n\n尚未取得可供整理的分析結果。";
  const highlights = (assessment.findings || []).slice(0, 3)
    .map((finding) => `- **${finding.title}**：${finding.text}`)
    .join("\n");
  const boundary = assessment.clinical_boundary
    ? `\n\n#### 判讀範圍\n> ${assessment.clinical_boundary}`
    : "";
  return `### 程式重點整理\n\n${assessment.summary}${highlights ? `\n\n#### 本次回看重點\n${highlights}` : ""}${boundary}`;
}

function renderLlmNarrative() {
  const narrative = state.analysis.llm_narrative || { status: "unavailable", text: null };
  const host = $("#llm-analysis-body");
  const statusText = {
    success: "已產生個人化解讀",
    disabled: "尚未啟用",
    fallback: "未能產生",
    unavailable: "目前無法使用",
  };
  const markdown = narrative.markdown || narrative.text;
  if (markdown) {
    $("#llm-analysis-status").textContent = statusText[narrative.status] || "已產生個人化解讀";
    host.classList.remove("is-unavailable");
    renderNarrativeMarkdown(host, markdown);
  } else {
    $("#llm-analysis-status").textContent = narrative.status === "disabled"
      ? "程式重點整理（LLM 未啟用）"
      : "程式重點整理（尚無 LLM 解讀）";
    host.classList.remove("is-unavailable");
    renderNarrativeMarkdown(host, computedNarrativeMarkdown());
  }
}

function renderReport() {
  const assessment = state.analysis.subject_assessment;
  const cadence = state.analysis.cadence;
  const primary = state.analysis.activation_consistency.priority_review[0];
  $("#report-recording").textContent = state.recording.replaceAll("_", " ");
  $("#stat-left-cadence").textContent = Number.isFinite(cadence?.left?.median_strokes_per_min)
    ? cadence.left.median_strokes_per_min.toFixed(0)
    : "—";
  $("#stat-right-cadence").textContent = Number.isFinite(cadence?.right?.median_strokes_per_min)
    ? cadence.right.median_strokes_per_min.toFixed(0)
    : "—";
  $("#stat-focus-muscle").textContent = primary?.muscle || "—";
  $("#stat-focus-score").textContent = Number.isFinite(primary?.cycle_consistency)
    ? `週期一致度 ${primary.cycle_consistency.toFixed(3)}`
    : "週期一致度 —";
  $("#report-headline").textContent = assessment.headline;
  $("#report-overview").textContent = assessment.summary;
  $("#program-findings").replaceChildren(...assessment.findings.map((finding) => {
    const item = document.createElement("li");
    const title = document.createElement("h3");
    const text = document.createElement("p");
    title.textContent = finding.title;
    text.textContent = finding.text;
    item.append(title, text);
    return item;
  }));
  $("#report-actions").replaceChildren(...assessment.recommended_checks.map((text) => {
    const item = document.createElement("li");
    item.textContent = text;
    return item;
  }));
  $("#report-limit").textContent = assessment.clinical_boundary;
  renderLlmNarrative();
}

function selectView(view) {
  state.activeView = view;
  document.querySelectorAll("[data-view-panel]").forEach((panel) => { panel.hidden = panel.dataset.viewPanel !== view; });
  document.querySelectorAll(".view-tab").forEach((button) => {
    const active = button.dataset.view === view;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", String(active));
  });
  window.setTimeout(() => document.querySelectorAll(".chart").forEach((chart) => {
    if (!chart.closest("[hidden]")) Plotly.Plots.resize(chart);
  }), 0);
}

function syncAllCharts() {
  if (state.syncing) return;
  state.syncing = true;
  syncIntervalSelector();
  [...document.querySelectorAll("[data-muscle-chart]")].map((element) => element.id)
    .concat("resistance-chart", "cadence-chart", "sync-left-chart", "sync-right-chart")
    .forEach((id) => Plotly.relayout(id, { "xaxis.range": range() }));
  state.syncing = false;
}

function syncIntervalSelector() {
  const end = Math.min(state.start + state.window, state.duration);
  const startSlider = $("#interval-start-slider");
  const endSlider = $("#interval-end-slider");
  startSlider.value = state.start;
  endSlider.value = end;
  $("#interval-value").textContent = `${timeLabel(state.start)}–${timeLabel(end)}`;
  const scale = Math.max(state.duration, 1);
  $("#interval-range").style.setProperty("--range-start", `${(state.start / scale) * 100}%`);
  $("#interval-range").style.setProperty("--range-end", `${(end / scale) * 100}%`);
}

function bindChartRange(id) {
  document.getElementById(id).on("plotly_relayout", (event) => {
    if (state.syncing || event["xaxis.range[0]"] === undefined || event["xaxis.range[1]"] === undefined) return;
    state.start = Math.max(0, Number(event["xaxis.range[0]"]));
    state.window = Math.max(1, Number(event["xaxis.range[1]"]) - state.start);
    syncAllCharts();
  });
}

async function loadRecording(recording) {
  state.recording = recording;
  const root = `data/${recording}`;
  const hasReport = state.reportAvailability.get(recording) === true;
  const [preview, cadence, metrics, metadata, analysis] = await Promise.all([
    fetchText(`${root}/synchronized_data/frontend_preview_10hz.csv`).then(parseCsv),
    fetchText(`${root}/pedaling_cadence/pedal_stroke_events.csv`).then(parseCsv),
    fetchText(`${root}/muscle_analysis/muscle_activation_metrics.csv`).then(parseCsv),
    fetchJson(`${root}/recording_metadata.json`),
    hasReport ? fetchJson(`${root}/analysis_report/analysis_summary.json`) : Promise.resolve(null),
  ]);
  if (!analysis) throw new Error(`紀錄 ${recording} 尚未產生分析報告`);
  state.preview = preview;
  state.cadence = cadence;
  state.metrics = metrics;
  state.metadata = metadata;
  state.analysis = analysis;
  state.muscles = analysis.muscles;
  colors = state.muscles.map((_, index) => css(`--color-series-${(index % 14) + 1}`));
  state.duration = Math.floor(preview.at(-1).time_s - preview[0].time_s);
  state.start = 0;
  state.window = Math.min(600, state.duration);
  $("#interval-start-slider").max = state.duration;
  $("#interval-end-slider").max = state.duration;
  syncIntervalSelector();
  renderSummary();
  renderReport();
  renderJointAnalysis();
  await Promise.all([makeEmgCharts(), makeResistanceChart(), makeCadenceChart(), makeSyncCharts()]);
  [...document.querySelectorAll("[data-muscle-chart]")].map((element) => element.id)
    .concat("resistance-chart", "cadence-chart", "sync-left-chart", "sync-right-chart")
    .forEach(bindChartRange);
  selectView(state.activeView);
}

async function boot() {
  const manifest = await fetchJson("data/manifest.json");
  state.reportAvailability = new Map(manifest.recordings.map(({ id, report_available }) => [id, report_available === true]));
  const selector = $("#recording-select");
  selector.replaceChildren(...manifest.recordings.map(({ id, label }) => new Option(label, id)));
  selector.addEventListener("change", () => loadRecording(selector.value));
  $("#interval-start-slider").addEventListener("input", (event) => {
    const end = Math.min(state.start + state.window, state.duration);
    state.start = Math.min(Number(event.target.value), Math.max(0, end - 1));
    state.window = Math.max(1, end - state.start);
    syncAllCharts();
  });
  $("#interval-end-slider").addEventListener("input", (event) => {
    const end = Math.max(Number(event.target.value), state.start + 1);
    state.window = end - state.start;
    syncAllCharts();
  });
  $("#print-report").addEventListener("click", () => window.print());
  document.querySelectorAll(".view-tab").forEach((button) => button.addEventListener("click", () => selectView(button.dataset.view)));
  await loadRecording(manifest.recordings[0].id);
}

boot().catch((error) => {
  const message = `載入資料失敗：${error.message}。請確認 GitHub Pages 已部署 docs/data，或以本機 HTTP 伺服器開啟，而非直接雙擊 index.html。`;
  $("#report-headline").textContent = message;
  ["#report-overview", "#report-limit", "#summary-text", "#joint-analysis", "#llm-analysis-status"].forEach((selector) => {
    const target = $(selector);
    if (target) target.textContent = message;
  });
  $("#llm-analysis-body").replaceChildren(Object.assign(document.createElement("p"), { textContent: message }));
  console.error(error);
});
