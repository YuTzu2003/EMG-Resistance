# EMG × Resistance 資料處理

本專案以 Delsys 官方 File Utility 將 EMGworks `.hpf` 匯出成 CSV，再用 Python 整理、濾波、計算 RMS、繪圖，並與 Resistance 資料做**訊號相位估計**。原始資料不會被改寫。

## 資料夾

```text
practice/
  Recording_A/
    EMG/EMG_Raw.hpf             # 原始 Delsys 檔
    EMG/EMG_Raw.csv             # File Utility 官方匯出，執行後產生
    Resistance/Resistance.csv  # 原始阻力資料
  Recording_B/                 # 同上；可另有原始 EMGworks 專案
emg_pipeline/                  # Python 程式，避免與專案名稱重複
tests/                         # 合成訊號測試
output/                        # 可重建的分析結果，不納入 Git
  Recording_A/
    emg/raw.csv                 # 整理後的原始訊號
    emg/bandpass.csv            # 20–450 Hz 帶通後訊號
    emg/rms.csv                 # 帶通後的 100 ms RMS
    plots/raw_channels.png
    plots/bandpass_channels.png
    plots/rms_channels.png
    plots/alignment_check.png
    plots/aligned_detail.png
    plots/aligned_overview.png
    plots/cadence.png
    sync/rms_resistance.csv
    sync/preview_10hz.csv
    cadence/stroke_events.csv
    analysis/muscle_metrics.csv
    metadata.json
  Recording_B/                 # 同樣的輸出結構
```

每個 EMG CSV 都有共用的 `time_s` 欄，後面是一欄一肌肉；單位沿用原始 HPF 的 V。官方匯出的每個通道都有獨立 `X[s]`，程式會驗證時間軸一致，去除尾端無時間的填補列。缺樣保留為空值，不當成 0，也不跨缺樣區段濾波。`metadata.json` 記錄 HPF 的通道、取樣率、缺樣數、處理參數與同步診斷。

## 安裝與執行

需要 Windows、Python 3.14 以上、[uv](https://docs.astral.sh/uv/) 與已安裝的 Delsys File Utility。在專案根目錄執行：

```powershell
uv sync
uv run python -m emg_pipeline Recording_A
uv run python -m emg_pipeline Recording_B
```

程式會尋找常見安裝位置的 `DelsysFileUtil.exe`，用官方命令列 `-nogui -o CSV -i <HPF>` 匯出。若安裝位置不同，加上 `--file-utility "C:\path\to\DelsysFileUtil.exe"`。已匯出過時，可節省轉檔時間：

```powershell
uv run python -m emg_pipeline Recording_A --use-exported-csv
uv run python -m emg_pipeline Recording_B --use-exported-csv
```

只做 EMG 處理、不做相位估計：

```powershell
uv run python -m emg_pipeline Recording_A --use-exported-csv --no-auto-sync
```

若日後找到**同一個可辨識事件**在兩套設備的時間點，可直接指定秒數，取代自動估計。以下數值僅為命令格式範例，**不是本次資料的實測事件**：

```powershell
uv run python -m emg_pipeline Recording_A --use-exported-csv --emg-event-s 12.5 --resistance-event-s 82.3
```

測試：`uv run python -m unittest discover -s tests -v`。

## EMG 圖與濾波定義

| 檔案 | 內容 | 是否可直接視為肌肉活化包絡 |
| --- | --- | --- |
| `plots/raw_channels.png` | 未額外處理的原始波形，每 300 點畫最小／最大值 | 否 |
| `plots/bandpass_channels.png` | 20–450 Hz、4 階 Butterworth 帶通；SOS 前後向濾波，零相位 | 否；仍是正負振盪波形 |
| `plots/rms_channels.png` | 帶通後平方、100 ms 置中移動平均、開根號 | 是；RMS 包絡 |

三張圖都涵蓋 14 個肌肉通道，橫軸是 EMG 起點起算的秒數，縱軸為 V。RMS 視窗約 215 點；開頭、結尾與缺樣附近沒有完整視窗的地方會留空。沒有預設加 50／60 Hz 陷波，因為尚未以頻譜確認電源雜訊，不能把陷波當成無代價的處理。這裡的 20–450 Hz 與 100 ms 是**本專案的分析設定**，不是宣稱重現 EMGworks 內部濾波。

## 與 Resistance 對齊

`sync/rms_resistance.csv` 保留原 Resistance 的角度、左右 crank 欄與時間戳，將每條 **RMS EMG** 線性插值到 Resistance 的時間點，只輸出共同時段，不外插。`plots/alignment_check.png` 畫前 12 秒左右股內肌 RMS 和各側 crank 絕對值；兩種振幅各自縮放至 0–1，**只能檢查相位，不能比較絕對振幅**。

兩個系統當時是人工同時按下執行，但檔案中沒有共同硬體 trigger 或可靠的跨設備絕對時鐘。因此自動流程先以兩檔首樣時間差作基準，在 ±5 秒範圍搜尋肌肉 RMS 與 crank 絕對值的相關峰。搜尋時使用 0.1 秒分箱、0.5 秒平滑與 20 秒趨勢去除；並檢查其他踩踏週期峰和三段資料的一致性。這是**活動相位對齊**，會混入生理上的肌肉到力量延遲，不能當作毫秒級設備同步。

Recording B 的左右股內肌支持同一相位。Recording A 的左右側不支持完全相同的相位：左股內肌與左股外肌一致偏好約 2.1 秒，右側兩條肌肉偏好約 2.4 秒。程式現在可用**左側兩條肌肉交叉確認**的相位產生 A 的合併檔，並在 `metadata.json` 明列右側不同步、逐段支持度和週期性歧義。A 的結果適合探索性分析，**不應解讀為已證實的精確同步**；若要分析左右反應時間差，仍需共同事件、錄影或硬體 trigger 驗證。舊的 `EMG_Torque_Avg.ipynb`／`EMG_Torque_Median.ipynb` 是依 crank 角度比較每圈的平均／中位數，不保留兩設備的絕對時間，不能單獨證明同步。

## 對齊後的圖與左右腳踩踏頻率

兩次錄製都會各自產生 `plots/aligned_detail.png`（對齊後前 30 秒）和 `plots/aligned_overview.png`（完整共同時段）。每張圖以同一時間軸排列左股內肌 RMS、左 crank、右股內肌 RMS、右 crank；上下分軸，避免把不同單位的振幅誤當可直接比較。圖上為顯示而做 0.05／0.2 秒分箱中位數，`sync/rms_resistance.csv` 仍保留完整 Resistance 時間點及全部 14 條 RMS EMG 通道。

若完整 CSV 筆數太多，可先開 `sync/preview_10hz.csv`：把每 0.1 秒的 EMG RMS 與左右 crank 取中位數，約 6,000 列，適合快速檢視與一般畫圖。它不包含跨越 0／360 度會產生錯誤中位數的 `Angle` 欄；需要原始時間精度或角度時，請用完整的 `sync/rms_resistance.csv`。**濾波不會減少取樣筆數**，預覽表的分箱才會減少列數。

這裡將「步頻」定義為**各腳每分鐘完成幾次同側踩踏**（strokes/min）；左、右各約一次／曲柄轉一圈。程式分別在有號的 `crankLeft`、`CrankRight` 上尋找正向峰，避免對訊號取絕對值後把一圈的正、負兩峰誤算成兩次。Resistance 資料先插值到 0.05 秒格點，僅為抓峰再做 0.25 秒平滑；峰至少相隔 0.6 秒，突出度門檻為該側訊號四分位距的 0.5 倍。相鄰同側峰的時間差為 `interval_s`，步頻為 `60 / interval_s`。第一個峰沒有前一峰，過短或過長（小於該側中位間隔的 0.65 倍或大於 1.5 倍）的間隔不計步頻，避免漏峰／誤峰直接變成假步頻。

`cadence/stroke_events.csv` 列出每個左右腳峰值、間隔、步頻與有效性；`plots/cadence.png` 顯示全程左右腳步頻的 7 次踩踏移動中位數；`metadata.json` 記錄左右腳偵測峰數、有效間隔數及步頻分布。左右腳各約 60 次／分鐘，若要口語的雙腳合計踩踏次數，約為 120 次／分鐘；**不要把單腳次數當成雙腳合計**。這是從 crank 力量峰推得的節律，不是鞋底接觸地面的步態偵測，也不是以 EMG 峰直接計步。實際峰值及例外間隔應以 CSV 和圖複核。

## GitHub Pages 靜態檢視介面

`docs/` 是不需要 Python 伺服器的前端頁面，適合直接由 GitHub Pages 發佈。先處理 A、B 紀錄，再匯出網頁資料：

```powershell
uv run python -m emg_pipeline.site_export
```

這只會寫入 `docs/data/` 的 10 Hz 檢視資料、步頻、週期一致度與去識別化的處理／同步摘要；不會複製 `.hpf`、原始 EMG CSV 或完整解析度的 `rms_resistance.csv`。將變更推到 GitHub 後，在 repository 的 **Settings → Pages** 選擇「Deploy from a branch」、分支 `main`（或你使用的發佈分支）、資料夾 `/docs`。GitHub 提供的 Pages 網址就是介面網址。

頁面上方是 **14 張獨立的肌肉 RMS 圖**，每一通道都有自己的縱軸，避免振幅大的肌肉壓縮其他曲線。每張圖的 `週期一致度` 會列在標題旁；越低代表逐圈活化形狀越不固定，並不代表肌力較弱。中段的左右阻力與步頻圖共用時間窗，拖曳任一張圖或移動滑桿會同步檢視範圍。最後一段列出同步處理步驟和該紀錄的相位估計結果。

圖表前的「自動分析報告」會依目前選取的 Recording A／B 產生文字摘要，列出最需要優先回看的肌肉、第二與第三順位、有效踩踏週期數、左右步頻分布、同步限制和下一步檢查方法。報告也會列出「左右相對活化差異」（同名左右肌肉的中位 RMS 差）與「活化波動」（P90／中位 RMS），協助找出值得對照阻力圖的時段。可按「列印／另存 PDF」輸出目前頁面。

這些都是 EMG 的**相對活化**指標，不是校正過的肌肉力量、關節力矩或功率。報告中的「做得較不平均」只代表逐圈 RMS 活化形狀一致度較低；左右 RMS 差異也可能來自電極位置、皮膚阻抗與組織差異。沒有 MVC 與校正過的扭力／功率資料時，不能將它寫成肌力不足、真實發力差或傷害診斷。

GitHub Pages 版是本專案唯一的互動介面；不需要也不保留 Streamlit 程式。

`analysis/muscle_metrics.csv` 的「週期一致度」是**活化時間型態**指標：用同側相鄰有效 crank 正峰界定踩踏週期，擷取每週期 5%–95% 相位的 20 個 EMG RMS 點；每圈先減去該圈平均並除以該圈的 RMS 尺度，再與本通道的逐相位中位波形比較相關程度，最後取各圈中位數。數值越低，表示該通道逐圈活化形狀越不固定。因為先去除振幅尺度，它**不能**代表肌肉發力較少，也不能跨肌肉比較強弱；感測器位置、動作變化、缺樣與相位估計也會影響數值。介面只把最低的三條列為「優先回看」，不是肌力、疲勞或傷害診斷。單點「本通道活化分位」也只比較同一通道於本次紀錄中的相對高低。

MVC（最大自主收縮）必須另外讓每條肌肉在標準姿勢與相同電極設置下做最大用力量測，才可計算 %MVC。現有 A、B 騎乘紀錄沒有這個基準，不能直接把不同肌肉的 µV 排名寫成「哪塊肌肉發揮比較不好」。A 的左右相位估計另有歧異，頁面會明列同步限制；A 的左右時差尤其不宜作精確結論。

## 參考資料

- [Delsys File Utility 文件](https://delsys.com/downloads/USERSGUIDE/emgworks/HTMLDocuments/delsysfileutility.htm)
- [Delsys 命令列範例](https://delsys.com/downloads/USERSGUIDE/emgworks/HTMLDocuments/examplematlabscript.htm)
- [Delsys sEMG 實務教材：濾波頻段](https://www.delsys.com/downloads/TUTORIAL/a-practicum-on-the-use-of-semg-signals-in-movement-sciences.pdf)
- [SciPy Butterworth 濾波器](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.butter.html)；[前後向零相位濾波](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.sosfiltfilt.html)
- [SciPy 峰值偵測與突出度](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.find_peaks.html)
- [CEDE 共識：EMG 振幅正規化方法](https://discovery.ucl.ac.uk/id/eprint/10106135/)
- [騎乘 EMG 正規化方法的信度研究](https://pmc.ncbi.nlm.nih.gov/articles/PMC4519210/)
