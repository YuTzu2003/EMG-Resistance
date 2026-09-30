const state = {
  recording: null, preview: [], cadence: [], metrics: [], metadata: null,
  analysis: null, muscles: [], reportAvailability: new Map(),
  start: 0, window: 600, syncing: false, activeView: "assessment", comparisonCache: new Map(),
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
  $(".control-bar").hidden = view === "compare";
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

async function loadComparisonRecording(recording) {
  if (!state.comparisonCache.has(recording)) {
    const root = `data/${recording}`;
    state.comparisonCache.set(recording, Promise.all([
      fetchText(`${root}/synchronized_data/frontend_preview_10hz.csv`).then(parseCsv),
      fetchText(`${root}/pedaling_cadence/pedal_stroke_events.csv`).then(parseCsv),
      fetchText(`${root}/muscle_analysis/muscle_activation_metrics.csv`).then(parseCsv),
      fetchJson(`${root}/recording_metadata.json`),
    ]).then(([preview, cadence, metrics, metadata]) => ({ recording, preview, cadence, metrics, metadata })));
  }
  return state.comparisonCache.get(recording);
}

function median(values) {
  const sorted = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (!sorted.length) return null;
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

function mean(values) {
  const finite = values.filter(Number.isFinite);
  return finite.length ? finite.reduce((total, value) => total + value, 0) / finite.length : null;
}

function comparisonMuscleLabel(channel) {
  const names = {
    "BICEPS FEMORIS": "股二頭肌", "VASTUS MEDIALIS": "股內側肌", "RECTUS FEMORIS": "股直肌",
    "VASTUS LATERALIS": "股外側肌", "TIBIALIS ANTERIOR": "脛前肌", "GASTROCNEMIUS": "腓腸肌", "SOLEUS": "比目魚肌",
  };
  const match = channel.match(/^([LR])\s+(.+?)(?:: EMG \d+)?$/);
  if (!match) return channel;
  return `${match[1] === "L" ? "左" : "右"}${names[match[2]] || match[2]}`;
}

function comparisonMetricLabel(metric) {
  return ({ median_rms_uv: "RMS 中位數 (µV)", cycle_consistency: "週期一致度", cadence: "踏頻 (同側踏／分)", resistance: "阻力絕對值中位數" })[metric];
}

function comparisonQuality(records) {
  const baseline = records[0];
  const warnings = ["公開比較資料未包含取樣率、電極位置或阻力設定；請以原始分析輸出確認這些條件。"];
  records.slice(1).forEach((record) => {
    if (JSON.stringify(record.metadata.processing) !== JSON.stringify(baseline.metadata.processing)) {
      warnings.push(`${record.recording} 的帶通或 RMS 處理設定與基準不同。`);
    }
    const channels = new Set(record.metrics.map((row) => row.channel));
    const missing = baseline.metrics.map((row) => row.channel).filter((channel) => !channels.has(channel));
    if (missing.length) warnings.push(`${record.recording} 缺少 ${missing.join("、")}。`);
  });
  return warnings;
}

async function renderComparison() {
  const baselineId = $("#comparison-baseline").value;
  const comparisonId = $("#comparison-recording").value;
  const quality = $("#comparison-quality");
  const table = $("#comparison-table");
  if (!baselineId || !comparisonId || baselineId === comparisonId) {
    quality.textContent = "請選擇兩筆不同的基準與比較資料。";
    table.replaceChildren();
    Plotly.purge("comparison-chart");
    return;
  }
  const [baseline, comparison] = await Promise.all([baselineId, comparisonId].map(loadComparisonRecording));
  populateComparisonMuscles(baseline);
  const metric = $("#comparison-metric").value;
  const channel = $("#comparison-muscle").value;
  const side = $("#comparison-side").value;
  const baselineValue = comparisonValue(baseline, metric, channel, side);
  const value = comparisonValue(comparison, metric, channel, side);
  const difference = Number.isFinite(baselineValue) && Number.isFinite(value) ? value - baselineValue : null;
  const percent = Number.isFinite(difference) && baselineValue !== 0 ? difference / Math.abs(baselineValue) * 100 : null;
  const rows = [{ record: comparison, value, difference, percent }];
  const warnings = comparisonQuality([baseline, comparison]);
  quality.textContent = warnings.length ? `可比性提醒：${warnings.join(" ")}` : "資料品質檢查：處理設定與肌肉通道可直接比較。";
  quality.classList.toggle("has-warning", warnings.length > 0);
  table.replaceChildren(...rows.map((row) => {
    const element = document.createElement("tr");
    [row.record.recording, baselineValue, row.value, row.difference, row.percent === null ? null : `${row.percent.toFixed(1)}%`]
      .forEach((value) => {
        const cell = document.createElement("td");
        cell.textContent = typeof value === "number" ? value.toFixed(3) : value ?? "N/A";
        element.append(cell);
      });
    return element;
  }));
  const data = [{ x: [baseline.recording, comparison.recording], y: [baselineValue, value], type: "bar", marker: { color: ["#1f5d84", "#00856a"] }, hovertemplate: "%{x}<br>%{y:.3f}<extra></extra>" }];
  Plotly.react("comparison-chart", data, { ...baseLayout(), height: 330, yaxis: { ...baseLayout().yaxis, title: comparisonMetricLabel(metric) } }, { responsive: true, displaylogo: false });
  const best = rows.filter((row) => Number.isFinite(row.percent)).sort((a, b) => Math.abs(b.percent) - Math.abs(a.percent))[0];
  const activationNote = metric === "median_rms_uv" ? " RMS 反映相對活化量，不能直接當作肌力。" : "";
  $("#comparison-draft").value = best
    ? `${channel || comparisonMetricLabel(metric)}：相較於 ${baseline.recording}，${best.record.recording} 的 ${comparisonMetricLabel(metric)} ${best.percent >= 0 ? "增加" : "降低"} ${Math.abs(best.percent).toFixed(1)}%。${activationNote}${warnings.length ? " 解讀前請先確認上方可比性提醒。" : " 可搭配其他肌肉與側別持續觀察。"}`
    : "選取指標在目前資料或時間區段沒有可比較的有效數值。";
}

function comparisonSamplingRate(metadata) {
  const exported = metadata.comparison_conditions?.sampling_rate_hz;
  if (typeof exported === "number") return exported;
  const rms = metadata.processing?.rms;
  return Number.isFinite(Number(rms?.window_samples)) && Number(rms?.window_s) > 0
    ? Number(rms.window_samples) / Number(rms.window_s) : null;
}

function comparisonQuality(records) {
  const [baseline, comparison] = records;
  const messages = [];
  const baselineRate = comparisonSamplingRate(baseline.metadata);
  const comparisonRate = comparisonSamplingRate(comparison.metadata);
  if (Number.isFinite(baselineRate) && baselineRate === comparisonRate) {
    messages.push(`已確認 EMG 取樣率相同：${baselineRate.toFixed(0)} Hz。`);
  } else if (Number.isFinite(baselineRate) && Number.isFinite(comparisonRate)) {
    messages.push(`取樣率不同：${baselineRate.toFixed(0)} Hz vs ${comparisonRate.toFixed(0)} Hz。`);
  } else {
    messages.push("取樣率無法從目前匯出的資料確認。");
  }
  const baselineProcessing = JSON.stringify(baseline.metadata.processing);
  const comparisonProcessing = JSON.stringify(comparison.metadata.processing);
  messages.push(baselineProcessing === comparisonProcessing
    ? "已確認帶通濾波與 RMS 設定相同。"
    : "帶通濾波或 RMS 設定不同，請避免直接判讀絕對差異。");
  const conditions = [baseline.metadata.comparison_conditions, comparison.metadata.comparison_conditions];
  if (!conditions.every((item) => item?.electrode_position_recorded)) {
    messages.push("電極位置未在原始 metadata 記錄，因此無法由系統確認兩次位置相同。");
  }
  if (!conditions.every((item) => item?.resistance_setting_recorded)) {
    messages.push("阻力設定未在原始 metadata 記錄，因此請以測試紀錄確認條件相同。");
  }
  return messages;
}

function comparisonChange(before, after) {
  const difference = Number.isFinite(before) && Number.isFinite(after) ? after - before : null;
  return { difference, percent: Number.isFinite(difference) && before !== 0 ? difference / Math.abs(before) * 100 : null };
}

function resistanceStats(record, side) {
  const column = side === "Left" ? "crankLeft" : "CrankRight";
  const values = record.preview.map((row) => Math.abs(Number(row[column]))).filter(Number.isFinite).sort((a, b) => a - b);
  if (!values.length) return { min: null, median: null, mean: null, max: null };
  return { min: values[0], median: median(values), mean: values.reduce((total, value) => total + value, 0) / values.length, max: values.at(-1) };
}

function appendComparisonCells(row, values) {
  values.forEach((value) => {
    const cell = document.createElement("td");
    cell.textContent = typeof value === "number" ? value.toFixed(3) : value ?? "N/A";
    row.append(cell);
  });
  return row;
}

function renderDefinitionList(host, entries) {
  const list = document.createElement("dl");
  entries.forEach(([label, value]) => {
    const term = document.createElement("dt");
    const detail = document.createElement("dd");
    term.textContent = label;
    detail.textContent = value;
    list.append(term, detail);
  });
  host.replaceChildren(list);
}

async function renderAllComparison() {
  const baselineId = $("#comparison-baseline").value;
  const comparisonId = $("#comparison-recording").value;
  const muscleTable = $("#comparison-table");
  const resistanceTable = $("#comparison-resistance-table");
  if (!baselineId || !comparisonId || baselineId === comparisonId) {
    muscleTable.replaceChildren();
    resistanceTable.replaceChildren();
    return;
  }
  const [baseline, comparison] = await Promise.all([baselineId, comparisonId].map(loadComparisonRecording));
  const muscles = baseline.metrics.map((before) => {
    const after = comparison.metrics.find((row) => row.channel === before.channel);
    const baselineMean = mean(baseline.preview.map((row) => Number(row[before.channel]) * 1e6));
    const comparisonMean = mean(comparison.preview.map((row) => Number(row[before.channel]) * 1e6));
    const average = comparisonChange(baselineMean, comparisonMean);
    const rms = comparisonChange(Number(before.median_rms_uv), Number(after?.median_rms_uv));
    const p90 = comparisonChange(Number(before.p90_rms_uv), Number(after?.p90_rms_uv));
    const consistency = comparisonChange(Number(before.cycle_consistency), Number(after?.cycle_consistency));
    return { before, after, baselineMean, comparisonMean, average, rms, p90, consistency };
  });
  muscleTable.replaceChildren(...muscles.map((entry) => appendComparisonCells(document.createElement("tr"), [
    comparisonMuscleLabel(entry.before.channel), entry.before.side === "Left" ? "左側" : "右側", entry.baselineMean, entry.comparisonMean,
    entry.average.percent === null ? null : `${entry.average.percent.toFixed(1)}%`,
    entry.p90.percent === null ? null : `${entry.p90.percent.toFixed(1)}%`,
    entry.consistency.difference,
  ])));
  const ordered = muscles.filter((entry) => Number.isFinite(entry.average.percent)).sort((a, b) => Math.abs(a.average.percent) - Math.abs(b.average.percent));
  Plotly.react("comparison-muscle-values-chart", [
    { x: muscles.map((entry) => comparisonMuscleLabel(entry.before.channel)), y: muscles.map((entry) => entry.baselineMean), name: baseline.recording, type: "bar", marker: { color: "#1f5d84" } },
    { x: muscles.map((entry) => comparisonMuscleLabel(entry.before.channel)), y: muscles.map((entry) => entry.comparisonMean), name: comparison.recording, type: "bar", marker: { color: "#00856a" } },
  ], { ...baseLayout(), height: 420, barmode: "group", xaxis: { tickangle: -45, gridcolor: css("--color-rule"), zerolinecolor: css("--color-rule") }, yaxis: { ...baseLayout().yaxis, title: "Mean RMS (µV)" } }, { responsive: true, displaylogo: false });
  Plotly.react("comparison-chart", [{
    x: ordered.map((entry) => entry.average.percent),
    y: ordered.map((entry) => comparisonMuscleLabel(entry.before.channel)),
    type: "bar", orientation: "h", marker: { color: ordered.map((entry) => entry.before.side === "Left" ? "#00856a" : "#7c3aed") },
    hovertemplate: "%{y}<br>Mean RMS change %{x:.1f}%<extra></extra>",
  }], { ...baseLayout(), height: Math.max(360, ordered.length * 28), xaxis: { title: "RMS median change (%)", gridcolor: css("--color-rule"), zeroline: true, zerolinecolor: css("--color-rule") }, yaxis: { ...baseLayout().yaxis, automargin: true } }, { responsive: true, displaylogo: false });
  const resistanceRows = [];
  const resistanceBySide = [];
  const categories = [];
  const baselineValues = [];
  const comparisonValues = [];
  ["Left", "Right"].forEach((side) => {
    const before = resistanceStats(baseline, side);
    const after = resistanceStats(comparison, side);
    resistanceBySide.push({ side, before, after });
    ["min", "median", "mean", "max"].forEach((statistic) => {
      const change = comparisonChange(before[statistic], after[statistic]);
      resistanceRows.push(appendComparisonCells(document.createElement("tr"), [side === "Left" ? "左側" : "右側", statistic, before[statistic], after[statistic], change.difference, change.percent === null ? null : `${change.percent.toFixed(1)}%`]));
      categories.push(`${side === "Left" ? "L" : "R"} ${statistic}`);
      baselineValues.push(before[statistic]);
      comparisonValues.push(after[statistic]);
    });
  });
  resistanceTable.replaceChildren(...resistanceRows);
  Plotly.react("comparison-resistance-chart", [
    { x: categories, y: baselineValues, name: baseline.recording, type: "bar", marker: { color: "#1f5d84" } },
    { x: categories, y: comparisonValues, name: comparison.recording, type: "bar", marker: { color: "#e8590c" } },
  ], { ...baseLayout(), height: 300, barmode: "group", xaxis: { gridcolor: css("--color-rule"), zerolinecolor: css("--color-rule") }, yaxis: { ...baseLayout().yaxis, title: "Absolute resistance (source unit)" } }, { responsive: true, displaylogo: false });
  const baselineMuscleMean = mean(muscles.map((entry) => entry.baselineMean));
  const comparisonMuscleMean = mean(muscles.map((entry) => entry.comparisonMean));
  const baselinePeak = [...muscles].filter((entry) => Number.isFinite(entry.baselineMean)).sort((a, b) => b.baselineMean - a.baselineMean)[0];
  const comparisonPeak = [...muscles].filter((entry) => Number.isFinite(entry.comparisonMean)).sort((a, b) => b.comparisonMean - a.comparisonMean)[0];
  renderDefinitionList($("#comparison-emg-analysis"), [
    [`${baseline.recording} 全肌肉平均 RMS`, Number.isFinite(baselineMuscleMean) ? `${baselineMuscleMean.toFixed(2)} µV` : "N/A"],
    [`${comparison.recording} 全肌肉平均 RMS`, Number.isFinite(comparisonMuscleMean) ? `${comparisonMuscleMean.toFixed(2)} µV` : "N/A"],
    [`${baseline.recording} 最高平均活化`, baselinePeak ? `${comparisonMuscleLabel(baselinePeak.before.channel)} (${baselinePeak.baselineMean.toFixed(2)} µV)` : "N/A"],
    [`${comparison.recording} 最高平均活化`, comparisonPeak ? `${comparisonMuscleLabel(comparisonPeak.before.channel)} (${comparisonPeak.comparisonMean.toFixed(2)} µV)` : "N/A"],
  ]);
  renderDefinitionList($("#comparison-resistance-analysis"), resistanceBySide.flatMap(({ side, before, after }) => [
    [`${side === "Left" ? "左" : "右"}側 ${baseline.recording} 平均／最大`, `${before.mean?.toFixed(3) ?? "N/A"} / ${before.max?.toFixed(3) ?? "N/A"}`],
    [`${side === "Left" ? "左" : "右"}側 ${comparison.recording} 平均／最大`, `${after.mean?.toFixed(3) ?? "N/A"} / ${after.max?.toFixed(3) ?? "N/A"}`],
  ]));
  const highlights = [...ordered].sort((a, b) => Math.abs(b.average.percent) - Math.abs(a.average.percent)).slice(0, 3)
    .map((entry) => `${comparisonMuscleLabel(entry.before.channel)} ${entry.average.percent >= 0 ? "增加" : "降低"} ${Math.abs(entry.average.percent).toFixed(1)}%`).join("；");
  const overallMuscleChange = comparisonChange(baselineMuscleMean, comparisonMuscleMean);
  const baselineResistanceMean = mean(resistanceBySide.map(({ before }) => before.mean));
  const comparisonResistanceMean = mean(resistanceBySide.map(({ after }) => after.mean));
  const overallResistanceChange = comparisonChange(baselineResistanceMean, comparisonResistanceMean);
  $("#comparison-overall-text").textContent = `完整紀錄比較顯示，全肌肉平均 RMS ${overallMuscleChange.percent === null ? "無法計算" : `${overallMuscleChange.percent >= 0 ? "增加" : "降低"} ${Math.abs(overallMuscleChange.percent).toFixed(1)}%`}；雙側平均阻力 ${overallResistanceChange.percent === null ? "無法計算" : `${overallResistanceChange.percent >= 0 ? "增加" : "降低"} ${Math.abs(overallResistanceChange.percent).toFixed(1)}%`}。肌肉變化最大者為：${highlights || "無可比較數值"}。RMS 為相對活化量，請與阻力及週期一致度一併解讀。`;
  $("#comparison-draft").value = `前測 ${baseline.recording} 與後測 ${comparison.recording} 的完整錄製比較：RMS 相對活化變化最大的肌肉為 ${highlights || "無可比較數值"}。RMS 是相對活化量，不能直接當作肌力；請連同 P90、週期一致度、踏頻、阻力與資料品質一起判讀。`;
}

function downloadComparisonDraft() {
  const text = $("#comparison-draft").value.trim();
  if (!text) return;
  const blob = new Blob([text + "\n"], { type: "text/markdown;charset=utf-8" });
  const link = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: "emg-comparison-summary.md" });
  link.click();
  URL.revokeObjectURL(link.href);
}

async function boot() {
  const manifest = await fetchJson("data/manifest.json");
  state.reportAvailability = new Map(manifest.recordings.map(({ id, report_available }) => [id, report_available === true]));
  const selector = $("#recording-select");
  selector.replaceChildren(...manifest.recordings.map(({ id, label }) => new Option(label, id)));
  const comparisonBaseline = $("#comparison-baseline");
  const comparisonRecording = $("#comparison-recording");
  const comparisonOptions = manifest.recordings.map(({ id, label }) => new Option(label, id));
  comparisonBaseline.replaceChildren(...comparisonOptions.map((option) => option.cloneNode(true)));
  comparisonRecording.replaceChildren(...comparisonOptions);
  if (comparisonRecording.options.length > 1) comparisonRecording.selectedIndex = 1;
  [comparisonBaseline, comparisonRecording]
    .forEach((element) => element.addEventListener("change", () => renderAllComparison()));
  $("#download-comparison").addEventListener("click", downloadComparisonDraft);
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
  await renderAllComparison();
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
