from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import pandas as pd
from .config import AppConfig
from .client import LLMError, generate_text

PROMPT_VERSION = "cycling-emg-report-v2"
MUSCLE_LABELS = {
    "R BICEPS FEMORIS": "右股二頭肌",
    "L BICEPS FEMORIS": "左股二頭肌",
    "R VASTUS MEDIALIS": "右股內側肌",
    "L VASTUS MEDIALIS": "左股內側肌",
    "R RECTUS FEMORIS": "右股直肌",
    "L RECTUS FEMORIS": "左股直肌",
    "R VASTUS LATERALIS": "右股外側肌",
    "L VASTUS LATERALIS": "左股外側肌",
    "R TIBIALIS ANTERIOR": "右脛前肌",
    "L TIBIALIS ANTERIOR": "左脛前肌",
    "R GASTROCNEMIUS": "右腓腸肌",
    "L GASTROCNEMIUS": "左腓腸肌",
    "R SOLEUS": "右比目魚肌",
    "L SOLEUS": "左比目魚肌",
}

def _muscle_key(channel: str) -> str:
    return channel.split(":", 1)[0].strip()

def _muscle_label(channel: str) -> str:
    return MUSCLE_LABELS.get(_muscle_key(channel), channel)

def _emg_resistance_relationship(recording_output: Path) -> dict:
    preview_path = recording_output / "synchronized_data" / "frontend_preview_10hz.csv"
    if not preview_path.is_file():
        return {"status": "unavailable", "reason": "frontend_preview_10hz.csv not found"}
    preview = pd.read_csv(preview_path)
    results = {}
    for side, prefix, resistance_column in (("left", "L ", "crankLeft"), ("right", "R ", "CrankRight")):
        channels = [column for column in preview.columns if column.startswith(prefix) and ": EMG " in column]
        if not channels or resistance_column not in preview:
            results[side] = {"status": "unavailable"}
            continue
        normalized = []
        for channel in channels:
            scale = float(preview[channel].quantile(0.9))
            if scale > 0:
                normalized.append(preview[channel] / scale * 100)
        envelope = pd.concat(normalized, axis=1).mean(axis=1) if normalized else pd.Series(dtype=float)
        correlation = envelope.corr(preview[resistance_column]) if len(envelope) else float("nan")
        results[side] = ({
            "status": "available",
            "zero_lag_pearson_r": round(float(correlation), 3),
            "emg_channels_in_composite": len(normalized),
        } if math.isfinite(correlation) else {"status": "unavailable", "reason": "constant or incomplete signal"})
    return {
        "status": "available",
        "method": "side-specific mean normalized RMS envelope versus same-side Resistance at zero lag",
        "interpretation": "describes simultaneous signal co-variation; not calibrated force contribution or causation",
        **results,
    }


def _subject_assessment(summary: dict) -> dict:
    """Turn calculated metrics into a recording-specific, non-diagnostic review."""
    focus = summary["activation_consistency"]["priority_review"]
    primary = focus[0]
    supporting = "、".join(item["muscle"] for item in focus[1:])
    average = summary["activation_consistency"]["all_muscle_average"]
    asymmetry = summary["relative_bilateral_activation"]["largest_difference"]
    variation = summary["activation_variation"]
    cadence = summary["cadence"]
    relationship = summary["emg_resistance_relationship"]

    findings = [
        {
            "key": "activation_consistency",
            "title": "逐圈活化穩定度",
            "text": (
                f"{primary['muscle']}的週期一致度最低（{primary['cycle_consistency']:.3f}），"
                f"低於全部肌肉平均（{average:.3f}）；其次為{supporting}。"
                "這表示同一踩踏週期中的活化形狀與峰值時機較常改變，應優先回看動作控制與量測品質。"
            ),
        },
        {
            "key": "activation_variation",
            "title": "活化波動",
            "text": (
                f"{variation['muscle']}的 P90／中位 RMS 比值最高（{variation['p90_to_median_ratio']:.2f} 倍）。"
                "這代表少數時段的活化明顯高於其平常水準，可對照當時的阻力、踏頻與姿勢變化。"
            ),
        },
    ]
    if asymmetry:
        findings.insert(1, {
            "key": "bilateral_activation",
            "title": "左右相對活化",
            "text": (
                f"{asymmetry['muscle']}的左右中位 RMS 差異最大，"
                f"{asymmetry['higher_side']}側約高 {asymmetry['median_rms_difference_percent']:.1f}%。"
                "這是本次紀錄中的相對活化不對稱，不等同校正後的左右力量差。"
            ),
        })
    if cadence.get("left") and cadence.get("right"):
        left = cadence["left"]
        right = cadence["right"]
        findings.append({
            "key": "cadence",
            "title": "踩踏節律",
            "text": (
                f"中位踩踏頻率為左 {left['median_strokes_per_min']:.1f}、"
                f"右 {right['median_strokes_per_min']:.1f} 次／分鐘。"
                "請以兩側節律是否在同一負荷區段同步改變，判斷不一致是否與踩踏節奏相關。"
            ),
        })
    if relationship.get("left", {}).get("status") == "available" and relationship.get("right", {}).get("status") == "available":
        findings.append({
            "key": "load_relationship",
            "title": "肌電與阻力共同變化",
            "text": (
                "同時間點的 EMG 彙整包絡與阻力相關為"
                f"左 r = {relationship['left']['zero_lag_pearson_r']:.3f}、"
                f"右 r = {relationship['right']['zero_lag_pearson_r']:.3f}。"
                "此結果只描述共同升降，不能視為肌肉產生阻力的比例或因果關係。"
            ),
        })

    actions = [
        f"先固定阻力與踏頻，確認{primary['muscle']}每圈的啟動與峰值時機是否變得集中。",
        f"同步回看{supporting}，判斷不一致是單一部位，還是同一動作鏈一起改變。",
    ]
    if asymmetry:
        actions.append(
            f"比較{asymmetry['muscle']}左右曲線的峰值時機，確認{asymmetry['higher_side']}側偏高是否持續存在。"
        )
    actions.extend([
        "下次量測維持相同電極位置、皮膚處理、座高與足部固定方式，減少非動作因素造成的差異。",
        "若要判定肌力、疲勞、受傷或真正左右出力差，需補做 MVC，並搭配校正過的扭力或功率量測。",
    ])
    return {
        "subject_term": "受測者",
        "headline": f"{primary['muscle']}是本次最需要回看的活化穩定度。",
        "summary": (
            f"本次資料顯示{primary['muscle']}的逐圈活化型態最不穩定。"
            "報告可指出相對活化不對稱、時序波動與負荷關係，但沒有 MVC 與校正扭力時，"
            "不能直接判定某條肌肉較弱或發力不足。"
        ),
        "findings": findings,
        "recommended_checks": actions,
        "clinical_boundary": (
            "這是單次騎乘的描述性動作分析，不是醫療診斷。"
            "肌電 RMS 代表量測到的電活動，不等同肌力、關節力矩或組織受傷。"
        ),
    }


def build_analysis_summary(recording_output: Path) -> dict:
    metadata_path = recording_output / "recording_metadata.json"
    metrics_path = recording_output / "muscle_analysis" / "muscle_activation_metrics.csv"
    missing = [str(path) for path in (metadata_path, metrics_path) if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Missing analysis files: {', '.join(missing)}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    raw_metrics = pd.read_csv(metrics_path)
    metrics = raw_metrics.sort_values("cycle_consistency", ignore_index=True)
    required = {
        "channel", "side", "median_rms_uv", "p90_rms_uv",
        "cycle_consistency", "usable_cycles", "missing_percent",
    }
    missing_columns = sorted(required.difference(metrics.columns))
    if missing_columns:
        raise ValueError(f"muscle_activation_metrics.csv missing columns: {', '.join(missing_columns)}")

    focus = [
        {
            "muscle": _muscle_label(row.channel),
            "channel": row.channel,
            "cycle_consistency": round(float(row.cycle_consistency), 4),
            "usable_cycles": int(row.usable_cycles),
        }
        for row in metrics.head(3).itertuples()
    ]
    by_key = {_muscle_key(row.channel): row for row in metrics.itertuples()}
    asymmetries = []
    for key, right in by_key.items():
        if not key.startswith("R "):
            continue
        left = by_key.get("L " + key[2:])
        if left is None:
            continue
        mean = (float(right.median_rms_uv) + float(left.median_rms_uv)) / 2
        difference = abs(float(right.median_rms_uv) - float(left.median_rms_uv)) / mean * 100 if mean else 0
        asymmetries.append({
            "muscle": _muscle_label(right.channel).removeprefix("右"),
            "higher_side": "右" if right.median_rms_uv > left.median_rms_uv else "左",
            "median_rms_difference_percent": round(difference, 1),
        })
    asymmetries.sort(key=lambda item: item["median_rms_difference_percent"], reverse=True)
    variation = metrics.assign(ratio=metrics["p90_rms_uv"] / metrics["median_rms_uv"]).sort_values("ratio").iloc[-1]
    synchronization = metadata.get("synchronization", {})
    summary = {
        "schema_version": "1.1",
        "recording": recording_output.name,
        "calculated_by": "emg_pipeline Python analysis; LLM did not calculate these values",
        "processing": metadata.get("processing", {}),
        "data_quality": {
            "channel_count": int(metadata.get("channel_count", len(metrics))),
            "sample_count": int(metadata.get("sample_count", 0)),
            "maximum_missing_percentage_points": round(float(metrics["missing_percent"].max()), 4),
        },
        "muscles": [
            {
                "channel": row.channel,
                "label": _muscle_label(row.channel),
                "side": row.side,
            }
            for row in raw_metrics.itertuples()
        ],
        "activation_consistency": {
            "interpretation": "lower values mean less repeatable cycle shape; not lower muscle strength",
            "priority_review": focus,
            "all_muscle_average": round(float(metrics["cycle_consistency"].mean()), 4),
        },
        "relative_bilateral_activation": {
            "interpretation": "within-recording RMS asymmetry; not calibrated force asymmetry",
            "largest_difference": asymmetries[0] if asymmetries else None,
        },
        "activation_variation": {
            "muscle": _muscle_label(variation["channel"]),
            "p90_to_median_ratio": round(float(variation["ratio"]), 2),
        },
        "cadence": metadata.get("cadence", {}),
        "emg_resistance_relationship": _emg_resistance_relationship(recording_output),
        "synchronization": {
            "status": synchronization.get("status"),
            "alignment_source": synchronization.get("alignment_source"),
            "emg_to_resistance_offset_s": synchronization.get("emg_to_resistance_offset_s"),
            "left_correlation": synchronization.get("left_correlation"),
            "right_correlation": synchronization.get("right_correlation"),
            "limitation": synchronization.get("limitation"),
        },
        "limitations": [
            "沒有 MVC 基準：RMS 振幅不能解讀為肌力或 %MVC。",
            "訊號相位對齊不是共同硬體 trigger。",
            "電極位置與皮膚阻抗可能影響左右振幅差異。",
            "本報告是描述性分析，不是醫療診斷。",
        ],
    }
    summary["subject_assessment"] = _subject_assessment(summary)
    return summary


def _llm_input(summary: dict, recording_output: Path, config: AppConfig) -> dict:
    payload = {"analysis_summary": summary, "data_scope": config.llm_data_scope}
    if config.llm_data_scope == "aligned_sample":
        preview_path = recording_output / "synchronized_data" / "frontend_preview_10hz.csv"
        if not preview_path.is_file():
            raise FileNotFoundError(f"Missing aligned preview: {preview_path}")
        preview = pd.read_csv(preview_path)
        step = max(1, len(preview) // config.llm_max_sample_rows)
        payload["aligned_preview_sample"] = preview.iloc[::step].head(config.llm_max_sample_rows).to_dict("records")
        payload["sample_note"] = "Bounded sample from processed 10 Hz aligned preview; not HPF or raw full-resolution EMG."
    return payload


def _calculated_markdown(summary: dict) -> str:
    focus = summary["activation_consistency"]["priority_review"]
    asymmetry = summary["relative_bilateral_activation"]["largest_difference"]
    cadence = summary["cadence"]
    relationship = summary["emg_resistance_relationship"]
    lines = [
        "## 程式計算結果",
        "",
        f"- 優先回看的活化穩定度：{focus[0]['muscle']}（{focus[0]['cycle_consistency']:.4f}），其次為"
        + "、".join(f"{item['muscle']}（{item['cycle_consistency']:.4f}）" for item in focus[1:]) + "。",
        f"- {summary['data_quality']['channel_count']} 條肌電通道平均週期一致度："
        f"{summary['activation_consistency']['all_muscle_average']:.4f}。",
    ]
    if asymmetry:
        lines.append(
            f"- 最大左右相對活化差異：{asymmetry['muscle']}，{asymmetry['higher_side']}側中位 RMS 約高 "
            f"{asymmetry['median_rms_difference_percent']:.1f}%。"
        )
    lines.extend([
        f"- 活化波動最高：{summary['activation_variation']['muscle']}，P90／中位 RMS = "
        f"{summary['activation_variation']['p90_to_median_ratio']:.2f}。",
    ])
    if cadence.get("left") and cadence.get("right"):
        lines.append(
            f"- 中位踩踏頻率：左 {cadence['left']['median_strokes_per_min']:.1f}、"
            f"右 {cadence['right']['median_strokes_per_min']:.1f} 次／分鐘。"
        )
    if relationship.get("left", {}).get("status") == "available" and relationship.get("right", {}).get("status") == "available":
        lines.append(
            f"- EMG 彙整包絡與同側 Resistance 的零位移相關：左 r = "
            f"{relationship['left']['zero_lag_pearson_r']:.3f}、右 r = "
            f"{relationship['right']['zero_lag_pearson_r']:.3f}；這是同步變化，不是校正力量或因果關係。"
        )
    return "\n".join(lines)


def _validate_narrative(narrative: str, summary: dict) -> None:
    """Reject LLM prose that duplicates numbers or contradicts available inputs."""
    if re.search(r"\d", narrative):
        raise LLMError("LLM narrative repeated or introduced numeric values")
    relationship = summary["emg_resistance_relationship"]
    if relationship.get("status") == "available":
        unavailable_claims = ("沒有阻力", "缺乏阻力", "無阻力", "未提供阻力")
        if any(claim in narrative for claim in unavailable_claims):
            raise LLMError("LLM narrative incorrectly claimed resistance data was unavailable")


def generate_report(recording_output: Path, config: AppConfig) -> dict:
    """Write deterministic JSON/Markdown, optionally enriched by an LLM."""
    summary = build_analysis_summary(recording_output)
    report_dir = recording_output / "analysis_report"
    report_dir.mkdir(parents=True, exist_ok=True)
    summary_path = report_dir / "analysis_summary.json"
    llm_payload = _llm_input(summary, recording_output, config)
    serialized = json.dumps(llm_payload, ensure_ascii=False, sort_keys=True)
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    trace = {
        "generated_at": generated_at,
        "prompt_version": PROMPT_VERSION,
        "input_sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        "data_scope": config.llm_data_scope,
        "report_template": config.report_template,
        "provider": config.llm_provider,
        "model": config.llm_model or None,
        "endpoint": config.llm_base_url or None,
        "llm_status": "disabled",
        "llm_error": None,
        "api_key_recorded": False,
    }
    narrative = None
    if config.llm_enabled:
        sections = (
            "受試者本次表現、優先修正線索、左右策略、阻力與節律、限制"
            if config.report_template == "rider"
            else "資料品質、方法、主要觀察、同步品質、限制"
        )
        system_prompt = (
            "你是運動科學報告撰寫助手。請針對這一位受測者或選手，"
            "只能根據使用者提供的 Python 結構化計算結果撰寫繁體中文報告；"
            "不可重新計算、不可診斷、不可把 RMS 稱為肌力、不可把相位對齊稱為硬體同步。"
            "程式計算結果已另列數字，因此本段不要重述任何數字、百分比、樣本數或相關係數；"
            "不得宣稱某種資料不存在，除非結構化欄位的 status 明確為 unavailable。"
            "要明確指出優先回看的肌肉、可能的左右活化策略差異、逐圈不穩定與阻力節律線索，"
            "並提出下一次可驗證的動作或量測方式。不可把線索寫成受傷、疲勞、肌肉無力或治療診斷。"
            f"請使用以下小節：{sections}。不要輸出一級標題。語氣具體且保守。"
        )
        user_prompt = f"提示詞版本：{PROMPT_VERSION}\n以下是分析輸入：\n{serialized}"
        try:
            response = generate_text(config, system_prompt, user_prompt)
            _validate_narrative(response.content, summary)
            narrative = response.content
            trace.update({"llm_status": "success", "provider": response.provider, "model": response.model, "endpoint": response.endpoint})
        except LLMError as exc:
            trace.update({"llm_status": "fallback", "llm_error": str(exc)})
    summary["llm_narrative"] = {
        "status": trace["llm_status"],
        "text": narrative,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_parts = [
        f"# {summary['recording']} EMG 與阻力分析報告",
        "",
        f"產出時間：{generated_at}",
        "",
        _calculated_markdown(summary),
        "",
    ]
    if narrative:
        report_parts.extend(["## LLM 產生的文字解讀", "", narrative, ""])
    else:
        report_parts.extend(["## LLM 產生的文字解讀", "", "本次未產生 LLM 文字；程式計算結果仍可獨立使用。", ""])
    report_path = report_dir / "analysis_report.md"
    trace_path = report_dir / "analysis_trace.json"
    report_path.write_text("\n".join(report_parts), encoding="utf-8")
    trace_path.write_text(json.dumps(trace, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"summary": summary_path, "report": report_path, "trace": trace_path, "trace_data": trace}
