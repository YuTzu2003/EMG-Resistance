# EMG-Resistance

自行車踩踏的 Delsys 肌電與阻力計分析工具。File Utility 呼叫實作獨立放在
`delsys_export.py`，一般仍由 `main.py run` 自動完成匯出、EMG 濾波、RMS 包絡、阻力資料對齊、
左右步頻分析、個別肌肉指標、Markdown 分析報告，以及 GitHub Pages 靜態前端資料。

## 1. 安裝環境

需求：Windows、Python 3.14 以上、[uv](https://docs.astral.sh/uv/)；匯出 HPF
還需要先安裝 **Delsys File Utility**。

```powershell
git clone https://github.com/YuTzu2003/EMG-Resistance.git
cd EMG-Resistance
uv sync
```

所有指令都從專案根目錄執行。查看完整參數：

```powershell
uv run python main.py --help
uv run python main.py run --help
```

## 2. 一次完成匯出與分析

只有 HPF 與阻力計 CSV 時，直接執行一個命令即可。若 HPF 旁邊已有同名 CSV，程式
直接使用；若沒有，`main.py` 會呼叫獨立的 `delsys_export.py` 自動匯出，再繼續分析：

```powershell
uv run python main.py run `
  --hpf-file "D:\實驗資料\受試者01\測試A.hpf" `
  --resistance-file "D:\實驗資料\受試者01\阻力計A.csv" `
  --output "D:\EMG分析結果\Participant_01_Test_A" `
  --no-llm
```

自動匯出的官方 CSV 會放在 HPF 旁邊，例如 `測試A.hpf` 產生 `測試A.csv`。若 CSV
在其他位置，可明確傳入：

```powershell
uv run python main.py run `
  --hpf-file "D:\實驗資料\受試者01\測試A.hpf" `
  --emg-csv "D:\官方匯出\測試A.csv" `
  --resistance-file "D:\實驗資料\受試者01\阻力計A.csv" `
  --output "D:\EMG分析結果\Participant_01_Test_A" `
  --no-llm
```

若 File Utility 不在標準安裝位置：

```powershell
uv run python main.py run `
  --hpf-file "D:\實驗資料\受試者01\測試A.hpf" `
  --resistance-file "D:\實驗資料\受試者01\阻力計A.csv" `
  --file-utility "C:\Program Files\Delsys, Inc\Delsys File Utility\DelsysFileUtil.exe" `
  --no-llm
```

`--output` 是這一次分析結果的完整資料夾路徑。指定後，`recording_metadata.json`、
`emg_signals/`、`synchronized_data/`、`analysis_report/` 等內容會直接放進該資料夾，
不會再自動增加一層名稱。
不同紀錄請指定不同資料夾，避免覆寫先前結果。

`--output` 可以省略。直接檔案模式會使用 HPF 檔名，自動建立
`output/<HPF檔名>/`；例如 `測試A.hpf` 會輸出到 `output/測試A/`。資料夾模式則
使用輸入資料夾名稱，例如 `Participant_01_Test_A` 會輸出到
`output/Participant_01_Test_A/`。

請勿將 `--output` 指向 `docs/` 或 `docs/data/`。這兩個位置只放前端需要的精簡資料；
程式完成分析後會自動更新，不需要手動把完整輸出放進去。

### 已整理成記錄資料夾時

舊的資料夾模式仍可使用，結構如下：

```text
Participant_01_Test_A/
  EMG/
    EMG_Raw.hpf
    EMG_Raw.csv          # 可預先存在；沒有時 main.py 自動匯出
  Resistance/
    Resistance.csv
```

```powershell
uv run python main.py run "D:\EMG_Data\Participant_01_Test_A" --no-llm
```

## 3. 會輸出哪些資料

假設使用 `--output "D:\EMG分析結果\Participant_01_Test_A"`，輸出結構為：

```text
Participant_01_Test_A/
  recording_metadata.json
  emg_signals/
    emg_raw.csv                         全部 EMG 通道、原始解析度、統一 time_s 欄位
    emg_bandpass_20_450hz.csv           20–450 Hz 零相位帶通後的全部通道
    emg_rms_100ms.csv                   100 ms 置中視窗 RMS 的全部通道
  synchronized_data/
    emg_rms_with_resistance.csv         阻力計全部欄位 + 全部肌肉 RMS 的整合資料
    frontend_preview_10hz.csv           前端使用的 10 Hz 精簡資料
  pedaling_cadence/
    pedal_stroke_events.csv             左右腳踩踏事件與逐圈步頻
  muscle_analysis/
    muscle_activation_metrics.csv       各肌肉逐圈一致性、RMS 分布與遺漏比例
  figures/
    emg_raw_overview.png
    emg_bandpass_20_450hz_overview.png
    emg_rms_100ms_overview.png
    synchronization_check.png
    synchronized_signals_first_30s.png
    synchronized_signals_overview.png
    pedaling_cadence.png
  analysis_report/
    analysis_summary.json    Python 結構化結果、個人評估與 LLM 文字
    analysis_report.md       僅包含程式計算結果與 LLM 文字解讀
    analysis_trace.json      報告來源、模型與產生紀錄
```

`emg_raw.csv`、`emg_bandpass_20_450hz.csv`、`emg_rms_100ms.csv` 都是完整 EMG 資料，
不是前端降採樣版本。`emg_rms_with_resistance.csv` 是正式的整合表：以阻力計時間戳
為共同時間軸，保留重疊區段，
把各肌肉 RMS 插值到阻力計時間點，不做範圍外外插。

原始 EMG、濾波 EMG 與 RMS 的取樣率很高，而阻力計有自己的取樣時間。因此程式
不會把三種 EMG 版本全部重複塞進同一個超大 CSV；需要原始波形時讀取
`emg_signals/`，需要肌肉活化與阻力的共同分析時讀取
`synchronized_data/emg_rms_with_resistance.csv`。

## 4. 時間同步

若沒有額外參數，程式會在開始時間相差不超過約 5 秒的前提下，用左右肌肉 RMS
活動相位與左右 crank 訊號估計偏移。這是訊號相位對齊，不等同共同硬體 trigger，
不能用來主張毫秒等級的神經肌肉反應延遲。

若同一同步事件在兩套設備中的時間點已知，應優先手動指定：

```powershell
uv run python main.py run `
  --hpf-file "D:\實驗資料\測試A.hpf" `
  --resistance-file "D:\實驗資料\阻力計A.csv" `
  --output "D:\EMG分析結果\Participant_01_Test_A" `
  --emg-event-s 12.35 `
  --resistance-event-s 13.02 `
  --no-llm
```

兩個事件參數必須一起提供。若目前只想匯出 raw、band-pass、RMS CSV，不進行同步、
步頻、報告與前端輸出，可加上 `--no-auto-sync`。

## 5. 分析報告與 LLM 設定

`--no-llm` 仍會產生完整的 Python 規則式報告，適合先測試資料流程。要使用本地
Ollama 或日後替換 OpenAI-compatible API，可複製設定檔：

```powershell
Copy-Item .env.example .env
```

再依環境修改 `.env`。數值、肌肉排名、左右相對活化、活化波動、步頻與阻力關係
都由 Python 計算；前端不會寫死特定肌肉。Python 會依每筆紀錄產生受測者評估與
後續回看項目，LLM 只負責把同一份結構化結果整理成個人化文字，不會重新計算。

沒有 MVC 與校正扭力／功率時，報告只會描述「相對活化不對稱」或「逐圈活化不穩定」，
不會直接宣稱某條肌肉較弱、受傷或發力不足。重新產生既有結果的報告：

```powershell
uv run python main.py report "D:\EMG分析結果\Participant_01_Test_A" --no-llm
```

移除 `--no-llm` 才會呼叫 `.env` 設定的模型。Ollama 連線會直接連到
`LLM_BASE_URL`，不使用系統 HTTP proxy；OpenAI-compatible provider 則維持一般
系統網路設定。Markdown 報告與前端評估頁只呈現兩類內容：程式計算結果與 LLM
產生的文字。模型、輸入雜湊與錯誤資訊仍獨立保存在 `analysis_trace.json`，不顯示於報告正文。

## 6. 本地查看靜態前端

分析完成後，程式會把前端需要的精簡資料更新到 `docs/data/`。不需要 Streamlit
或專用後端，可在 VS Code 對 `docs/index.html` 使用 **Live Server**；也可以執行：

`docs/data/<紀錄名稱>/` 會保留和 `output/<紀錄名稱>/` 相同的相對路徑與正式檔名，
但只複製前端需要的 metadata、10 Hz 預覽、步頻、肌肉指標與報告，不會複製大型的
原始 EMG、完整同步表或 PNG 圖片。每次新增或更新一筆紀錄時，也會保留 manifest
裡其他已匯出的紀錄。

前端會從 `analysis_summary.json` 取得實際肌肉清單與受測者結論，不預設固定的問題
部位。所有 Plotly 圖表統一使用淺色背景，而且 Plotly 已放在 `docs/assets/vendor/`，
本機查看不需要連外載入圖表套件。

```powershell
python -m http.server 8000 --directory docs
```

然後開啟 `http://127.0.0.1:8000/`。不要直接雙擊 HTML 用 `file://` 開啟，瀏覽器
通常會阻擋頁面載入本地 JSON/CSV，畫面就會一直顯示「正在讀取報告」。

GitHub Pages 只需要部署 `docs/` 靜態內容，不需要額外 server。請注意：執行分析只會
更新本機檔案，必須自行確認資料已去識別化，再提交要公開的 `docs/data/`。

## 7. 驗收與常見錯誤

驗證某次分析結果、報告與前端檔案是否齊全：

```powershell
uv run python main.py verify "D:\EMG分析結果\Participant_01_Test_A"
```

常見錯誤：

- `DelsysFileUtil.exe not found`：在 `main.py run` 傳入 `--file-utility`。
- `Officially exported CSV not found`：確認 `--emg-csv` 路徑，或不要指定它，讓程式自動匯出。
- `Resistance CSV needs a Timestamp (s) column`：阻力 CSV 必須含遞增的
  `Timestamp (s)` 欄位。
- `Delsys CSV ... disagree`：CSV 通道、時間欄或取樣率與 HPF 標頭不一致，請用同一
  份 HPF 重新執行官方匯出。
- 自動同步失敗：先檢查左右 crank 欄位與訊號品質；若有共同事件，改用
  `--emg-event-s` 與 `--resistance-event-s`。

程式碼基本檢查：

```powershell
uv run python -m compileall main.py emg_pipeline
```
