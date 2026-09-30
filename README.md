# EMG-Resistance

將 Delsys EMG 與阻力計資料做濾波、RMS、時間同步、肌肉週期指標與靜態視覺化。

- EMG：4 階零相位 Butterworth 20–450 Hz 帶通，接 100 ms RMS。
- 網站：單筆檢視與兩筆前後資料對照；公開資料僅含 10 Hz 預覽與彙總指標。
- 比較：Python 先計算數值與可比性，再選擇性由 LLM 根據結構化結果產生待確認草稿。

## 快速開始

```powershell
uv sync
uv run python main.py --help
uv run python main.py run "D:\EMG_Data\Participant_01_Test_A" --no-llm
```

完整安裝、資料夾格式、處理指令、LLM 設定、網站預覽、驗證與多筆比較操作請見[使用測試說明](使用測試說明.md)。

## 驗證

```powershell
uv run python -m unittest discover -s tests -v
```
