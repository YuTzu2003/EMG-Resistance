# EMG-Resistance

自行車踩踏的 Delsys 肌電與阻力計分析工具。程式可以從 `.hpf` 呼叫官方
Delsys File Utility 匯出 CSV，接著完成 EMG 濾波、RMS 包絡、阻力資料對齊、
左右步頻分析、個別肌肉指標、Markdown 分析報告，以及 GitHub Pages 靜態前端資料。

## 1. 安裝環境

需求：Windows、Python 3.14 以上、[uv](https://docs.astral.sh/uv/)；若要從
HPF 自動匯出，還需要先安裝 **Delsys File Utility**。

```powershell
git clone https://github.com/YuTzu2003/EMG-Resistance.git
cd EMG-Resistance
uv sync
```

所有指令都從專案根目錄執行，統一入口是 `main.py`。查看完整參數：

```powershell
uv run python main.py --help
uv run python main.py run --help
```

## 2. 只有 HPF 與阻力計 CSV 時

不必先建立固定名稱的 `EMG/` 或 `Resistance/` 資料夾。直接把兩個來源檔案的
路徑傳給程式即可：

```powershell
uv run python main.py run `
  --hpf-file "D:\實驗資料\受試者01\測試A.hpf" `
  --resistance-file "D:\實驗資料\受試者01\阻力計A.csv" `
  --recording-name "Participant_01_Test_A" `
  --output-root "D:\EMG分析結果" `
  --no-llm
```

這個模式會先用 Delsys File Utility 把 HPF 匯成同位置、同檔名的 CSV，例如
`測試A.hpf` 會產生 `測試A.csv`，再繼續分析。若 File Utility 不在標準安裝位置：

```powershell
uv run python main.py run `
  --hpf-file "D:\實驗資料\測試A.hpf" `
  --resistance-file "D:\實驗資料\阻力計A.csv" `
  --recording-name "Participant_01_Test_A" `
  --file-utility "C:\Program Files\Delsys, Inc\Delsys File Utility\DelsysFileUtil.exe" `
  --no-llm
```

`--output-root` 可以省略，預設為專案內的 `output`。`--recording-name` 也可以
省略，此時會使用 HPF 檔名；建議明確填寫受試者與測試名稱，之後較好辨識。

### 已經有 File Utility 匯出的 EMG CSV

用 `--emg-csv` 指定它，程式就不會再次匯出 HPF：

```powershell
uv run python main.py run `
  --hpf-file "D:\實驗資料\測試A.hpf" `
  --emg-csv "D:\實驗資料\測試A_官方匯出.csv" `
  --resistance-file "D:\實驗資料\阻力計A.csv" `
  --recording-name "Participant_01_Test_A" `
  --no-llm
```

HPF 仍然需要提供，因為程式會從 HPF 標頭讀取通道名稱、單位與取樣率，並用來
檢查官方匯出的 CSV 是否對應正確。

## 3. 已整理成記錄資料夾時

舊的資料夾模式仍可使用，結構如下：

```text
Participant_01_Test_A/
  EMG/
    EMG_Raw.hpf
    EMG_Raw.csv          # 已經用 File Utility 匯出時才需要
  Resistance/
    Resistance.csv
```

讓程式自行匯出 HPF：

```powershell
uv run python main.py run "D:\EMG_Data\Participant_01_Test_A" --no-llm
```

若 `EMG_Raw.csv` 已存在，略過 File Utility 匯出：

```powershell
uv run python main.py run "D:\EMG_Data\Participant_01_Test_A" --use-exported-csv --no-llm
```

## 4. 會輸出哪些資料

假設 `--recording-name` 是 `Participant_01_Test_A`，輸出會放在：

```text
<output-root>/Participant_01_Test_A/
  metadata.json
  emg/
    raw.csv                  全部 EMG 通道、原始解析度、統一 time_s 欄位
    bandpass.csv             20–450 Hz 零相位帶通後的全部通道
    rms.csv                  100 ms 置中視窗 RMS 的全部通道
  sync/
    rms_resistance.csv       阻力計全部欄位 + 全部肌肉 RMS 的整合資料
    preview_10hz.csv         前端使用的 10 Hz 精簡資料
  cadence/
    stroke_events.csv        左右腳踩踏事件與逐圈步頻
  analysis/
    muscle_metrics.csv       各肌肉逐圈一致性、RMS 分布與遺漏比例
  plots/
    *.png                    濾波、RMS、同步、步頻等檢查圖
  report/
    analysis_summary.json    Python 計算出的結構化分析結果
    report.md                可閱讀的分析報告
    trace.json               報告來源、模型與產生紀錄
```

`raw.csv`、`bandpass.csv`、`rms.csv` 都是完整 EMG 資料，不是前端降採樣版本。
`rms_resistance.csv` 是正式的整合表：以阻力計時間戳為共同時間軸，保留重疊區段，
把各肌肉 RMS 插值到阻力計時間點，不做範圍外外插。

原始 EMG、濾波 EMG 與 RMS 的取樣率很高，而阻力計有自己的取樣時間。因此程式
不會把三種 EMG 版本全部重複塞進同一個超大 CSV；需要原始波形時讀取 `emg/`，
需要肌肉活化與阻力的共同分析時讀取 `sync/rms_resistance.csv`。

## 5. 時間同步

若沒有額外參數，程式會在開始時間相差不超過約 5 秒的前提下，用左右肌肉 RMS
活動相位與左右 crank 訊號估計偏移。這是訊號相位對齊，不等同共同硬體 trigger，
不能用來主張毫秒等級的神經肌肉反應延遲。

若同一同步事件在兩套設備中的時間點已知，應優先手動指定：

```powershell
uv run python main.py run `
  --hpf-file "D:\實驗資料\測試A.hpf" `
  --resistance-file "D:\實驗資料\阻力計A.csv" `
  --recording-name "Participant_01_Test_A" `
  --emg-event-s 12.35 `
  --resistance-event-s 13.02 `
  --no-llm
```

兩個事件參數必須一起提供。若目前只想匯出 raw、band-pass、RMS CSV，不進行同步、
步頻、報告與前端輸出，可加上 `--no-auto-sync`。

## 6. 分析報告與 LLM 設定

`--no-llm` 仍會產生完整的 Python 規則式報告，適合先測試資料流程。要使用本地
Ollama 或日後替換 OpenAI-compatible API，可複製設定檔：

```powershell
Copy-Item .env.example .env
```

再依環境修改 `.env`。數值、排名與指標由 Python 計算，LLM 只負責把結構化結果
整理成文字，不應自行產生新的數值結論。重新產生既有結果的報告：

```powershell
uv run python main.py report "D:\EMG分析結果\Participant_01_Test_A" --no-llm
```

## 7. 本地查看靜態前端

分析完成後，程式會把前端需要的精簡資料更新到 `docs/data/`。不需要 Streamlit
或專用後端，可在 VS Code 對 `docs/index.html` 使用 **Live Server**；也可以執行：

```powershell
python -m http.server 8000 --directory docs
```

然後開啟 `http://127.0.0.1:8000/`。不要直接雙擊 HTML 用 `file://` 開啟，瀏覽器
通常會阻擋頁面載入本地 JSON/CSV，畫面就會一直顯示「正在讀取報告」。

GitHub Pages 只需要部署 `docs/` 靜態內容，不需要額外 server。請注意：執行分析只會
更新本機檔案，必須自行確認資料已去識別化，再提交要公開的 `docs/data/`。

## 8. 驗收與常見錯誤

驗證某次分析結果、報告與前端檔案是否齊全：

```powershell
uv run python main.py verify "D:\EMG分析結果\Participant_01_Test_A"
```

常見錯誤：

- `DelsysFileUtil.exe not found`：安裝 File Utility，或傳入 `--file-utility`。
- `Officially exported CSV not found`：確認 `--emg-csv` 路徑，或讓程式重新匯出。
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
