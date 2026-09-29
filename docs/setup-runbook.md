# 会場用ランブック：YAM × DGX Spark を電源投入から動かすまで

対象：研究室メンバー全員（初めて触る人向け）。作成 2026-09-30、**確認済みの手順だけ**を書く。未確認の項目は「⏳」。
詳しい背景は `reference.md`、部品の写真と配線の理屈は `docs/hardware-wiring.md`。

## 0. 持ち物チェック（会場に持ち込む前に）

- [ ] YAM 左右（フレームに固定済み）、カメラポール
- [ ] 24 V / 14 A アダプタ ×2 + AC コード ×2
- [ ] CANable（USB-C 基板）×2、**「R」「L」のラベルを貼ってあるもの**。分岐ハーネス（黄 XT30 + 黒 4 ピン）付き
- [ ] USB-C ケーブル ×2（CANable 用）
- [ ] DGX Spark 本体 + 純正 240 W 電源
- [ ] Anker ハブ + 付属 AC アダプタ
- [ ] RealSense D435 ×1、D405 ×2、USB ケーブル
- [ ] スイッチ付き電源タップ（**非常停止の代わり**。キットに E-stop は無い）
- [ ] モニタ、キーボード、マウス（Spark のトラブル時用）

## 1. 配線（すべて電源 OFF で行う）

```text
YAM 右 根元 4 芯ケーブル ── 黒 4 ピン ── ハーネス R ─┬─ 黄 XT30 ── 24 V アダプタ R ── AC（タップ）
                                                    └─ CANable R ── USB-C ── Anker ハブ ── Spark
YAM 左 ── 同じ構成で ハーネス L / アダプタ L / CANable L
Spark ── 純正電源（左端の USB-C）。Anker ハブは Spark のデータ用 USB-C に接続し、ハブ自身の AC アダプタも挿す
```

1. Spark に純正電源、Anker ハブ、ハブの AC アダプタ、LAN を接続。**カメラと CANable はまだ挿さない。**
2. Spark の電源を入れ、起動を待つ（ログイン画面まで 1〜2 分）。
3. CANable R をハブの **5 Gbps ポート**に挿す（ラベルどおり R を右腕のハーネスに）。
4. 黒 4 ピンを右腕根元のケーブルへ、黄 XT30 をアダプタ R へ。AC はまだ挿さない。
5. 右腕が畳まれて安定していること、可動範囲に物が無いことを確認。
6. アダプタ R の AC をタップへ。**タップのスイッチ ON = 通電**。各関節が一瞬「コッ」と鳴る。
7. 左腕も同様（CANable L、ハーネス L、アダプタ L）。⏳

## 2. Spark 側の確認（ターミナルで）

CANable は serial 番号で名前が固定されている（`/etc/udev/rules.d/90-can.rules`）。挿せば自動で `can_follower_r` / `can_follower_l` になる。

```bash
ip -br link show type can          # can_follower_r / can_follower_l が見えるか
sudo ip link set can_follower_r up type can bitrate 1000000   # DOWN なら（再起動のたびに必要）
```

⏳ 再起動後に自動で up する設定は未導入。

## 3. 動作確認（右腕）

```bash
cd ~/molmoact2-setup/workspaces/lerobot-client && source .venv/bin/activate && cd i2rt
python i2rt/motor_config_tool/ping_motors.py --channel can_follower_r      # motor 1〜7 が応答するか
# 腕を手で支えてから：
python i2rt/robots/motor_chain_robot.py --channel can_follower_r --gripper_type linear_4310 --operation_mode gravity_comp
```

✅ 2026-09-30 右腕：`ping_motors.py` で `online motors: [1, 2, 3, 4, 5, 6, 7]`、全て `error_message='normal'`。これが出れば配線は正しい。
✅ 2026-09-30 右腕：重力補償 125〜130 Hz で安定、グリッパ自動キャリブレーション OK。ログに `Grav Comp Control Frequency: 1xx Hz` が出て 100 Hz を下回らなければ正常。
起動・停止は `bash ~/molmoact2-setup/gravcomp.sh start|stop|status can_follower_r` でもできる（ログは `~/molmoact2-setup/logs/gravcomp_<iface>.log`）。

観測値を 1 回読むだけなら（腕を支えて）：
```bash
python ~/molmoact2-setup/read_obs_once.py can_follower_r
```
✅ 2026-09-30 右腕：`joint_pos` 6 個、`gripper_pos` 1 個（0=閉 … 1=開）が返る。

## 3.5 カメラの確認

カメラは **Spark 起動後**に挿す。D435（俯瞰）は Spark の USB-C に直挿し、D405（手首）は Anker の 5 Gbps USB-A に USB-A↔Micro-B ケーブルで。

```bash
cd ~/molmoact2-setup/workspaces/lerobot-client && source .venv/bin/activate
lerobot-find-cameras realsense       # serial が 3 つ出れば OK（top 138422071505 / right 260522272252 / left ⏳）
lsusb -t | grep -i video             # 全部 5000M であること。480M なら挿し直す
```

✅ 2026-09-30：top と right を 640×360@30 で取得、各 30.9 fps。2 台同時 60 s でも 30.0 fps・ドロップ 0。
同時取得の確認：`python ~/molmoact2-setup/tools/scripts/camera_stream_test.py --seconds 60 top=138422071505 right=260522272252`（`exit=0` なら OK）
「0 台」と出たら、`ls -l /dev/video0` が `plugdev` になっているか確認（udev ルールと video グループは設定済み。カメラの挿し直しで直ることが多い）。

## 3.6 記録・共有用の撮影

**カメラ映像を MP4 で保存**（Spark 上、複数台同時。既定 20 秒、640×360@30）：
```bash
cd ~/molmoact2-setup/workspaces/lerobot-client && source .venv/bin/activate
python ~/molmoact2-setup/tools/scripts/camera_record.py --seconds 20 top=138422071505 right=260522272252
# 保存先: ~/molmoact2-setup/logs/videos/<日時>_<role>.mp4（先頭フレームの PNG も同名で保存）
```
Mac に取り込んで Slack 向け H.264 に変換：`bash scripts/fetch_videos.sh` → `logs/videos-<日付>/*_h264.mp4`

**重力補償の様子をスマホで撮る**：腕を支えて `bash ~/molmoact2-setup/gravcomp.sh start can_follower_r`、撮影後に腕を支えて `... stop can_follower_r`。

## 4. 停止・片付け

1. 実行中のプログラムを Ctrl+C。
2. **腕を手で支えながら**タップのスイッチ OFF（重力補償が切れると腕が落ちる）。
3. Spark はデスクトップからシャットダウン、またはターミナルで `sudo poweroff`。
4. USB を抜くのは Spark の電源が切れてから。

## 5. 困ったとき

| 症状 | 対応 |
|---|---|
| `ip -br link show type can` に何も出ない | CANable の USB を挿し直す。`lsusb \| grep 1d50` に出るか確認 |
| ハブごと消えた（キーボードも効かない） | ハブの USB-C を Spark から抜いて挿し直す（2026-09-30 に発生） |
| `Device or resource busy` | `bash ~/molmoact2-setup/workspaces/lerobot-client/i2rt/scripts/reset_all_can.sh` |
| 腕が急に脱力した | 400 ms タイムアウト（指令が途絶えた）。プログラムを止めて原因を見る |
| 異常音・発煙 | **即タップ OFF**。再開しない |

## 6. 変更履歴

- 2026-09-30: 初版。右腕の CANable（serial 208E37AE45465006）を `can_follower_r` に割当
