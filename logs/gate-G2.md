# Gate G2

| 項目 | 値 |
|---|---|
| 日付 | 2026-09-30 |
| 担当 / 監視 | |
| Spark OS / driver / CUDA | |
| 使用 commit（client / server / i2rt / official） | |

## 実行したこと
- 2026-09-30 再起動後：D435 + D405 ×2 が全て 5000M で認識（起動前から挿してあっても USB 2.0 固定は起きなかった）。左 D405 serial 260322279321
- 2026-09-30 02:19: D405（右手首）を Anker 5 Gbps USB-A に接続 → `8086:0b5b`、uvcvideo、**5000M**（ハブ 2 段目）
- 2026-09-30: `lerobot-find-cameras realsense` が 0 台 → 原因は権限（`/dev/video*` が root:video 660、ユーザーが video グループ外、librealsense udev ルール未導入）。`setup_realsense_perms.sh`（Intel 公式 `99-realsense-libusb.rules` + `usermod -aG video`）で解決
- 2026-09-30 02:22: D435 を Spark 直挿し（Bus 004）。pyrealsense2 で 2 台検出

## 結果（貼り付けたログの要約）
| Role | 型番 | Serial | FW | 接続 | 640×360@30 RGB |
|---|---|---|---|---|---|
| top | D435 | **138422071505** | 5.15.0.2 | Spark 直挿し（USB-C） | 対応 |
| right | D405 | **260522272252** | 5.15.1.55 | Anker 5 Gbps USB-A | 対応 |
| left | D405 | **260322279321** | | Anker 5 Gbps USB-A | 対応（同型） |

- 単体取得（LeRobot、640×360@30、warmup 2 s）：top 30.9 fps、right 30.9 fps。画像 `logs/cameras-2026-09-30/`（top = 俯瞰で両腕がフレーム内、right = 畳んだ手首から天井向き）
- **2 台同時 60 s**（`scripts/camera_stream_test.py`）：top 1801 / right 1802 frames = **30.0 fps 両方、read 失敗 0、200 ms 超のギャップ 0、最大ギャップ 35 ms**。試験中のカーネル USB/xHCI メッセージなし。（単体試験の切断時に `uvcvideo 4-1:1.4: Failed to resubmit video URB (-1)` が 1 件、D435 の stop 時刻と一致。実害なし）

## 問題と対応
- 2026-09-30: LeRobot `RealSenseCamera.read()` の初回が `status=False` で失敗 → pyrealsense2 直叩きは正常（top 77 / right 76 frames in 3 s）。**`warmup_s=2` を付けると 60/60 成功、30.9 fps**。robot_client の camera 設定にも `"warmup_s": 2` を入れる

- **3 台同時 60 s**（再起動後、D435 直挿し + D405 ×2 Anker）：top 1801 / right 1802 / left 1802 frames = **30.0 fps ×3、失敗 0、ギャップ 0、最大 35 ms**、カーネル USB エラーなし
- 本番用設定 `configs/dry_run.yaml`（3 serial 入り）を作成し Spark の `~/molmoact2-setup/tools/configs/` に配置

## 次へ進む条件の充足
- [x] top / right / left の RGB 画像と serial/role の対応を保存（2026-09-30、`logs/cameras-2026-09-30/`）
- [x] left D405 の接続・serial 固定（260322279321）
- [x] 3 台同時取得 60 s、30 fps、ドロップ 0（2026-09-30）
- **G2 完了（2026-09-30）**
