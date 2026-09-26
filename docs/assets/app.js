const MUSCLES = [
  ["R BICEPS FEMORIS: EMG 1", "右股二頭肌"], ["L BICEPS FEMORIS: EMG 2", "左股二頭肌"],
  ["R VASTUS MEDIALIS: EMG 3", "右股內側肌"], ["L VASTUS MEDIALIS: EMG 4", "左股內側肌"],
  ["R RECTUS FEMORIS: EMG 5", "右股直肌"], ["L RECTUS FEMORIS: EMG 6", "左股直肌"],
  ["R VASTUS LATERALIS: EMG 7", "右股外側肌"], ["L VASTUS LATERALIS: EMG 8", "左股外側肌"],
  ["R TIBIALIS ANTERIOR: EMG 10", "右脛前肌"], ["L TIBIALIS ANTERIOR: EMG 11", "左脛前肌"],
  ["R GASTROCNEMIUS: EMG 12", "右腓腸肌"], ["L GASTROCNEMIUS: EMG 13", "左腓腸肌"],
  ["R SOLEUS: EMG 14", "右比目魚肌"], ["L SOLEUS: EMG 16", "左比目魚肌"],
];

const state = {
  recording: null, preview: [], cadence: [], metrics: [], metadata: null,
  analysis: null, trace: null, reportMarkdown: null, reportAvailability: new Map(),
  start: 0, window: 30, syncing: false, activeView: "assessment",
};
const $ = (selector) => document.querySelector(selector);
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const colors = Array.from({ length: MUSCLES.length }, (_, index) => css(`--color-series-${index + 1}`));

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

function rollingMedian(values, size = 7) {
  const half = Math.floor(size / 2);
  return values.map((_, index) => percentile(values.slice(Math.max(0, index - half), index + half + 1), 0.5));
}

function elapsed(row) { return row.time_s - state.preview[0].time_s; }
function range() { return [state.start, Math.min(state.start + state.window, state.duration)]; }
function labelFor(channel) { return MUSCLES.find(([id]) => id === channel)?.[1] || channel; }
function timeText(seconds) { return `${seconds.toFixed(0)}–${Math.min(seconds + state.window, state.duration).toFixed(0)} 秒`; }

function baseLayout(isDark = false) {
  const foreground = isDark ? css("--color-graphite-text") : css("--color-text");
  const rule = isDark ? css("--color-rule-strong") : css("--color-rule");
  return {
    paper_bgcolor: "transparent", plot_bgcolor: "transparent", font: { family: css("--font-body"), color: foreground },
    margin: { l: 58, r: 28, t: 22, b: 56 }, hovermode: "x unified", dragmode: "pan",
    xaxis: { title: "對齊後經過時間（秒）", range: range(), gridcolor: rule, zerolinecolor: rule },
    yaxis: { gridcolor: rule, zerolinecolor: rule },
    legend: { orientation: "h", y: -0.24, x: 0, font: { size: 11 }, groupclick: "toggleitem" },
  };
}

function makeEmgCharts() {
  const x = state.preview.map(elapsed);
  const host = $("#emg-charts");
  host.replaceChildren(...MUSCLES.map(([channel, label], index) => {
    const metric = state.metrics.find((entry) => entry.channel === channel);
    const card = document.createElement("article");
    const id = `emg-chart-${index}`;
    card.className = "muscle-chart-card";
    card.innerHTML = `<div class="muscle-chart-heading"><h3>${label}</h3><p>週期一致度 ${metric.cycle_consistency.toFixed(3)}</p></div><div id="${id}" class="chart muscle-chart" data-muscle-chart aria-label="${label} RMS 活化曲線"></div>`;
    return card;
  }));
  return Promise.all(MUSCLES.map(([channel, label], index) => {
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
  const traces = [
    ["crankLeft", "左側阻力", colors[5]], ["CrankRight", "右側阻力", colors[8]],
  ].map(([channel, label, color]) => ({ x, y: state.preview.map((row) => row[channel]), name: label, type: "scattergl", mode: "lines", line: { color, width: 1.6 }, hovertemplate: `%{x:.2f} 秒<br>${label}：%{y:.2f}<extra></extra>` }));
  const layout = { ...baseLayout(true), yaxis: { ...baseLayout(true).yaxis, title: "Resistance 原始單位" }, height: 300 };
  return Plotly.react("resistance-chart", traces, layout, { responsive: true, displaylogo: false, scrollZoom: true });
}

function makeCadenceChart() {
  const origin = state.preview[0].time_s;
  const traces = ["Left", "Right"].map((side, index) => {
    const rows = state.cadence.filter((row) => row.side === side && Number.isFinite(row.strokes_per_min));
    const smoothed = rollingMedian(rows.map((row) => row.strokes_per_min));
    const label = side === "Left" ? "左腳" : "右腳";
    return { x: rows.map((row) => row.time_s - origin), y: smoothed, name: label, type: "scatter", mode: "lines", line: { color: colors[index ? 8 : 5], width: 2 }, hovertemplate: `%{x:.1f} 秒<br>${label}：%{y:.1f} 次/分<extra></extra>` };
  });
  const layout = { ...baseLayout(true), yaxis: { ...baseLayout(true).yaxis, title: "同側踩踏次數／分鐘" }, height: 260 };
  return Plotly.react("cadence-chart", traces, layout, { responsive: true, displaylogo: false, scrollZoom: true });
}

function meanNormalizedEnvelope(side) {
  const channels = MUSCLES.filter(([channel]) => channel.startsWith(`${side} `)).map(([channel]) => channel);
  const scales = new Map(channels.map((channel) => [channel, percentile(
    state.preview.map((row) => Number(row[channel]) * 1e6), 0.9
  )]));
  return state.preview.map((row) => {
    const values = channels.map((channel) => Number(row[channel]) * 1e6 / scales.get(channel)).filter(Number.isFinite);
    return values.length ? values.reduce((total, value) => total + value, 0) / values.length * 100 : null;
  });
}

function pearsonCorrelation(first, second) {
  const pairs = first.map((value, index) => [value, second[index]])
    .filter(([left, right]) => Number.isFinite(left) && Number.isFinite(right));
  if (pairs.length < 3) return null;
  const firstMean = pairs.reduce((total, [value]) => total + value, 0) / pairs.length;
  const secondMean = pairs.reduce((total, [, value]) => total + value, 0) / pairs.length;
  const numerator = pairs.reduce((total, [left, right]) => total + (left - firstMean) * (right - secondMean), 0);
  const firstScale = Math.sqrt(pairs.reduce((total, [value]) => total + (value - firstMean) ** 2, 0));
  const secondScale = Math.sqrt(pairs.reduce((total, [, value]) => total + (value - secondMean) ** 2, 0));
  return firstScale && secondScale ? numerator / (firstScale * secondScale) : null;
}

function correlationText(value) {
  if (!Number.isFinite(value)) return "資料不足，無法計算";
  if (Math.abs(value) < 0.2) return "關聯很低";
  return value > 0 ? "大致同向變化" : "大致反向變化";
}

function makeSideSyncChart(side, chartId, emgColor, resistanceColor) {
  const x = state.preview.map(elapsed);
  const traces = [
    { y: meanNormalizedEnvelope(side), name: `${side === "L" ? "左" : "右"}側 EMG 彙整包絡`, color: emgColor, axis: "y" },
    { y: state.preview.map((row) => row[side === "L" ? "crankLeft" : "CrankRight"]), name: `${side === "L" ? "左" : "右"}側阻力`, color: resistanceColor, axis: "y2" },
  ].map(({ y, name, color, axis }) => ({
    x, y, name, yaxis: axis, type: "scattergl", mode: "lines",
    line: { color, width: axis === "y" ? 2 : 1.35 },
    hovertemplate: `%{x:.2f} 秒<br>${name}：%{y:.2f}<extra></extra>`,
  }));
  const layout = {
    ...baseLayout(), height: 260,
    yaxis: { ...baseLayout().yaxis, title: "EMG 彙整包絡（各肌肉 P90 = 100%）", rangemode: "tozero" },
    yaxis2: { title: "Resistance 原始單位", overlaying: "y", side: "right", gridcolor: "transparent", zerolinecolor: "transparent" },
  };
  return Plotly.react(chartId, traces, layout, { responsive: true, displaylogo: false, scrollZoom: true });
}

function makeSyncCharts() {
  return Promise.all([
    makeSideSyncChart("L", "sync-left-chart", colors[5], colors[11]),
    makeSideSyncChart("R", "sync-right-chart", colors[8], colors[2]),
  ]);
}

function renderSummary() {
  const focus = [...state.metrics].sort((a, b) => a.cycle_consistency - b.cycle_consistency).slice(0, 3);
  $("#focus-list").replaceChildren(...focus.map((item, index) => {
    const entry = document.createElement("li");
    entry.innerHTML = `<span class="focus-rank">0${index + 1}</span><span class="focus-muscle">${labelFor(item.channel)}</span><span class="focus-score">${item.cycle_consistency.toFixed(3)}</span>`;
    return entry;
  }));
  const names = focus.map((item) => labelFor(item.channel)).join("、");
  $("#summary-text").textContent = `這位受試者在${names}的逐圈活化形狀相對較不一致。下方每條圖的「週期一致度」越接近 1，代表各圈的活化時序越相似；可優先觀察這三處在不同阻力與踏頻下，是否出現足踝或膝部控制時機改變。這是動作與訊號的回看線索，並非肌肉較弱、疲勞或受傷的判定。`;
}

function renderJointAnalysis() {
  const leftCorrelation = pearsonCorrelation(meanNormalizedEnvelope("L"), state.preview.map((row) => row.crankLeft));
  const rightCorrelation = pearsonCorrelation(meanNormalizedEnvelope("R"), state.preview.map((row) => row.CrankRight));
  const leftText = Number.isFinite(leftCorrelation) ? leftCorrelation.toFixed(2) : "—";
  const rightText = Number.isFinite(rightCorrelation) ? rightCorrelation.toFixed(2) : "—";
  $("#joint-analysis").textContent = `整段資料中，左側 EMG 彙整包絡與左側阻力的零位移相關為 r = ${leftText}（${correlationText(leftCorrelation)}）；右側為 r = ${rightText}（${correlationText(rightCorrelation)}）。這個數值只描述兩條訊號在相同時間點的共同升降，不代表 EMG 直接等於發力或阻力；請以同步折線圖查看特定時間段的峰值與時序。`;
}

function renderGeneratedReport() {
  const host = $("#generated-report-body");
  const source = $("#generated-report-source");
  if (!state.reportMarkdown || !state.analysis || !state.trace) {
    source.textContent = "這筆紀錄尚未匯出分析報告。";
    const message = document.createElement("p");
    message.textContent = "請先執行 python main.py report，或用 python main.py run 重新分析。";
    host.replaceChildren(message);
    return;
  }

  const generatedAt = new Date(state.trace.generated_at).toLocaleString("zh-TW");
  const model = state.trace.provider === "none"
    ? "純 Python"
    : `${state.trace.provider} / ${state.trace.model}`;
  source.textContent = `${generatedAt} · ${model} · ${state.trace.data_scope} · ${state.trace.llm_status}`;

  const fragment = document.createDocumentFragment();
  let list = null;
  state.reportMarkdown.split(/\r?\n/).forEach((rawLine) => {
    const line = rawLine.trim();
    if (!line) { list = null; return; }
    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      list = null;
      const element = document.createElement(heading[1].length === 1 ? "h3" : "h4");
      element.textContent = heading[2];
      fragment.append(element);
      return;
    }
    if (line.startsWith("- ")) {
      if (!list) {
        list = document.createElement("ul");
        fragment.append(list);
      }
      const item = document.createElement("li");
      item.textContent = line.slice(2);
      list.append(item);
      return;
    }
    list = null;
    const paragraph = document.createElement("p");
    paragraph.textContent = line;
    fragment.append(paragraph);
  });
  host.replaceChildren(fragment);
}

function renderReport() {
  const ordered = [...state.metrics].sort((a, b) => a.cycle_consistency - b.cycle_consistency);
  const primary = ordered[0];
  const supporting = ordered.slice(1, 3);
  const average = ordered.reduce((total, item) => total + item.cycle_consistency, 0) / ordered.length;
  const primaryName = labelFor(primary.channel);
  const supportingNames = supporting.map((item) => labelFor(item.channel)).join("、");
  const cadence = state.metadata.cadence;
  const left = cadence.left;
  const right = cadence.right;
  const sync = state.metadata.synchronization;
  const sideWarning = sync.alignment_source === "estimated_signal_phase_left_reference";
  const pairAsymmetry = MUSCLES.filter(([channel]) => channel.startsWith("R ")).map(([rightChannel, rightName]) => {
    const leftName = rightName.replace(/^右/, "左");
    const leftChannel = MUSCLES.find(([, label]) => label === leftName)?.[0];
    const rightMetric = state.metrics.find((item) => item.channel === rightChannel);
    const leftMetric = state.metrics.find((item) => item.channel === leftChannel);
    if (!rightMetric || !leftMetric) return null;
    const mean = (rightMetric.median_rms_uv + leftMetric.median_rms_uv) / 2;
    const difference = Math.abs(rightMetric.median_rms_uv - leftMetric.median_rms_uv) / mean * 100;
    const higherSide = rightMetric.median_rms_uv > leftMetric.median_rms_uv ? "右" : "左";
    return { name: rightName.replace(/^右/, ""), difference, higherSide };
  }).filter(Boolean).sort((a, b) => b.difference - a.difference)[0];
  const largestVariation = [...state.metrics].map((item) => ({
    ...item, ratio: item.p90_rms_uv / item.median_rms_uv,
  })).sort((a, b) => b.ratio - a.ratio)[0];

  $("#report-headline").textContent = `本次騎乘的第一個修正焦點是${primaryName}的活化穩定度：同一圈踩踏中，它的啟動與峰值時機較常改變。先把它當成動作一致性的線索，而不是肌力不足的結論。`;
  $("#report-primary").textContent = `${primaryName} 的週期一致度為 ${primary.cycle_consistency.toFixed(3)}，低於本次 14 條肌肉平均 ${average.toFixed(3)}；${primary.usable_cycles} 個有效週期中，${supportingNames}也呈現較低一致度。建議先在固定阻力下回看這些肌肉的峰值是否每圈出現在相近時段，再觀察踏頻、座位或足踝控制改變時是否更不穩定。`;
  $("#report-context").textContent = `左腳中位踩踏頻率為 ${left.median_strokes_per_min.toFixed(0)} 次／分（10–90 百分位 ${left.p10_strokes_per_min.toFixed(1)}–${left.p90_strokes_per_min.toFixed(1)}），右腳為 ${right.median_strokes_per_min.toFixed(0)} 次／分（${right.p10_strokes_per_min.toFixed(1)}–${right.p90_strokes_per_min.toFixed(1)}）。兩側節律可先作為基線；若低一致度只發生在特定阻力或踏頻範圍，優先從那個情境修正動作。`;
  $("#report-asymmetry").textContent = `同名肌肉中，${pairAsymmetry.name}的左右中位 RMS 差異最大：${pairAsymmetry.higherSide}側約高 ${pairAsymmetry.difference.toFixed(0)}%。這提示左右側的相對活化策略不同；請在相同阻力下比較兩側峰值時機。它不等同真正的左右發力差，電極位置與皮膚阻抗也會影響振幅。`;
  $("#report-variation").textContent = `${labelFor(largestVariation.channel)}的 P90／中位 RMS 比值最高（${largestVariation.ratio.toFixed(1)} 倍），代表少數踩踏週期的活化高於平常水準較多。若這些高峰只在特定阻力段出現，可先從該負荷下的踏頻與姿勢穩定度著手觀察。`;
  const actions = [
    `固定阻力與踏頻後，先回看${primaryName}每圈的峰值時機是否更集中。`,
    `同時比較${pairAsymmetry.name}的左右曲線，確認較高活化的一側是否每圈都持續偏高。`,
    "下次量測維持相同電極位置與皮膚處理，避免感測位置差異被誤認為活化差異。",
    "若目標是判定肌肉力量或左右真實發力差異，需另外量測每條肌肉的 MVC，並結合校正過的扭力／功率資料。",
  ];
  $("#report-actions").replaceChildren(...actions.map((text) => {
    const item = document.createElement("li");
    item.textContent = text;
    return item;
  }));
  $("#report-limit").textContent = sideWarning
    ? "限制：Recording A 以左側訊號相位作為合併參考，而右側偏好相位不同。因此可用來檢查整體活化型態，但不應把左右時間差解讀成精確的生理反應時間。"
    : "限制：此處是訊號相位對齊，而不是共同硬體 trigger。報告反映活化時序的一致性，不能診斷傷害、疲勞或肌力。";
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
  $("#time-slider").value = state.start;
  $("#time-value").textContent = timeText(state.start);
  [...document.querySelectorAll("[data-muscle-chart]")].map((element) => element.id)
    .concat("resistance-chart", "cadence-chart", "sync-left-chart", "sync-right-chart")
    .forEach((id) => Plotly.relayout(id, { "xaxis.range": range() }));
  state.syncing = false;
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
  const [preview, cadence, metrics, metadata, analysis, trace, reportMarkdown] = await Promise.all([
    fetchText(`${root}/preview_10hz.csv`).then(parseCsv),
    fetchText(`${root}/stroke_events.csv`).then(parseCsv),
    fetchText(`${root}/muscle_metrics.csv`).then(parseCsv),
    fetchJson(`${root}/metadata.json`),
    hasReport ? fetchJson(`${root}/analysis_summary.json`) : Promise.resolve(null),
    hasReport ? fetchJson(`${root}/trace.json`) : Promise.resolve(null),
    hasReport ? fetchText(`${root}/report.md`) : Promise.resolve(null),
  ]);
  state.preview = preview;
  state.cadence = cadence;
  state.metrics = metrics;
  state.metadata = metadata;
  state.analysis = analysis;
  state.trace = trace;
  state.reportMarkdown = reportMarkdown;
  state.duration = Math.floor(preview.at(-1).time_s - preview[0].time_s);
  state.start = 0;
  state.window = Number($("#window-select").value);
  $("#time-slider").max = Math.max(0, state.duration - state.window);
  $("#time-slider").value = 0;
  $("#time-value").textContent = timeText(0);
  renderSummary();
  renderReport();
  renderGeneratedReport();
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
  $("#time-slider").addEventListener("input", (event) => { state.start = Number(event.target.value); syncAllCharts(); });
  $("#window-select").addEventListener("change", (event) => {
    state.window = Number(event.target.value);
    $("#time-slider").max = Math.max(0, state.duration - state.window);
    state.start = Math.min(state.start, Number($("#time-slider").max));
    syncAllCharts();
  });
  const dialog = $("#command-palette");
  $("#command-button").addEventListener("click", () => dialog.showModal());
  $("#print-report").addEventListener("click", () => window.print());
  document.querySelectorAll(".view-tab").forEach((button) => button.addEventListener("click", () => selectView(button.dataset.view)));
  document.querySelectorAll(".command-view").forEach((button) => button.addEventListener("click", () => {
    selectView(button.dataset.view);
    dialog.close();
    $(".view-tabs").scrollIntoView({ behavior: "smooth", block: "start" });
  }));
  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") { event.preventDefault(); dialog.showModal(); }
  });
  await loadRecording(manifest.recordings[0].id);
}

boot().catch((error) => {
  const message = `載入資料失敗：${error.message}。請確認 GitHub Pages 已部署 docs/data，或以本機 HTTP 伺服器開啟，而非直接雙擊 index.html。`;
  $("#report-headline").textContent = message;
  ["#report-primary", "#report-context", "#report-asymmetry", "#report-variation", "#report-limit", "#summary-text", "#joint-analysis", "#generated-report-source"].forEach((selector) => {
    const target = $(selector);
    if (target) target.textContent = message;
  });
  $("#generated-report-body").replaceChildren(Object.assign(document.createElement("p"), { textContent: message }));
  console.error(error);
});
