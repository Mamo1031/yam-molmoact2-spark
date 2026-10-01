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
7. 左腕も同様（CANable L、ハーネス L、アダプタ L）。✅ 2026-09-30 確認

## 2. Spark 側の確認（ターミナルで）

CANable は serial 番号で名前が固定されている（`/etc/udev/rules.d/90-can.rules`）。挿せば自動で `can_follower_r` / `can_follower_l` になる。

```bash
ip -br link show type can          # can_follower_r / can_follower_l が見えるか
sudo ip link set can_follower_r up type can bitrate 1000000   # DOWN なら（再起動のたびに必要）
```

⏳ 再起動後に自動で up する設定は未導入（`sudo ip link set can_follower_r up type can bitrate 1000000` と `_l` を毎回実行）。

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

### 3.4 両腕をまとめて立ち上げる（LeRobot の follower server）

```bash
cd ~/molmoact2-setup/workspaces/lerobot-client && source .venv/bin/activate
python -m lerobot.scripts.setup_bi_yam_servers --eval     # 右 = port 1234、左 = port 1235
```
✅ 2026-09-30：両腕とも重力補償で待機し、robot_client と同じ経路で 14 次元 state が取れた。止めるときは Ctrl+C（腕を支えてから）。
バックグラウンド（`nohup ... &`、ログは `~/molmoact2-setup/logs/bi_yam_servers.log`）で起動した場合は Ctrl+C 相当の SIGINT が効かない。**両腕を支えてから** `pkill -f "[m]inimum_gello"` で止める（左右とも脱力する）。

## 3.5 カメラの確認

カメラは **Spark 起動後**に挿す。D435（俯瞰）は Spark の USB-C に直挿し、D405（手首）は Anker の 5 Gbps USB-A に USB-A↔Micro-B ケーブルで。

```bash
cd ~/molmoact2-setup/workspaces/lerobot-client && source .venv/bin/activate
lerobot-find-cameras realsense       # serial が 3 つ出れば OK（top 138422071505 / right 260522272252 / left 260322279321）
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

## 3.7 実機動作（G5、教員承認済み）— guarded executor

**前提**：follower servers（§3.4）と FastAPI server（8202）が動いている。両腕は start pose 付近。停止担当が電源タップに手を添えている。
Spark 上のラッパー `~/molmoact2-setup/g5` が venv の有効化と既定オプション（`--yes --start-pose-tolerance 0.2`、return-to-start は `--max-step 0.004`）を付けてくれる。Mac の Terminal.app から：

```bash
# 0) start pose 合わせ（ライブ表示。両腕を手で動かして全部 ok にする）
ssh -t spark '~/molmoact2-setup/g5 pose-guide'

# 1) 方策なしの確認（初回や配線変更後だけ）
ssh -t spark '~/molmoact2-setup/g5 direction-test --arms right'
ssh -t spark '~/molmoact2-setup/g5 hold --seconds 10'

# 2) 本番：両腕、上限 0.01 rad/tick、40 chunk（約 50 s）。--chunks / --max-step / --instruction を変えて試す
ssh -t spark '~/molmoact2-setup/g5 run --arms both --max-step 0.01 --chunks 40 --steps-per-chunk 20 --instruction "pick up the box and place it on the side"'

# 3) 試行後に start pose へ戻す（0.004 rad/tick 固定）
ssh -t spark '~/molmoact2-setup/g5 return-to-start'

# 4) ログの要約とトレース（<run_dir> は実行時に表示される）
ssh -t spark '~/molmoact2-setup/g5 analyze <run_dir>'
ssh -t spark '~/molmoact2-setup/g5 trace <run_dir> --arm right --every 30'
```

- 出力先：`~/molmoact2-setup/logs/raw/g5_<mode>_<日時>/`（`ticks.csv`、`chunks.jsonl`、`summary.json`、`images/chunkNNN_{top,left,right}.png` = 方策が見た画像）
- 主なオプション：`--chunks`（推論回数、1 chunk ≈ 0.67 s 動作 + 0.47 s 推論）、`--steps-per-chunk`（1 chunk のうち実行する step 数、既定 20/30）、`--max-step`（rad/tick、0.01 が公式補間と同じ。0.02 まで試してよい）、`--arms left|right|both`、`--instruction`（小文字）、`--hold-wait`（異常保持後にループを抜けるまでの秒数、既定 5）
- 実行中のキー（executor を動かしたターミナル）：Enter = 保持、q = 終了、o = グリッパを開く、r = start pose へ戻る
- 異常で保持したときは、腕は follower server が保持し続ける。`return-to-start` で戻せる。動かなくなったら `pkill -f "[r]un_policy_guarded"`
- 安全設定は `configs/dry_run.yaml` の `safety:` / `execution:`（乖離閾値、範囲 margin、床余裕、start pose、モータ温度）
- **グリッパのガードは無効**（2026-10-01、硬い物を掴むため）：`configs/dry_run.yaml` の `tracking_err_max` のグリッパ 2 箇所と `chunk_*_max_gripper` が 1.0。握力の上限は i2rt 側の 50 N 制限だけなので、**グリッパに指を入れない**。異常で保持に入ってもグリッパは握ったまま（物を落とさない）。離すのは Enter（全部力を抜く）か、保持中の o。`r` は握ったまま start pose へ戻る。別プロセスの `g5 return-to-start` / `open-grippers` は最初の指令が実測値なので、その時点で物が離れる（先に置く）。空のグリッパで動作確認するときは 1.0 を 0.2 に戻す（CLI フラグは無い）
- **モータ監視**（2026-09-30 追加）：executor は CAN を受信専用で読み、(1) bus が無音（= follower server がモータを駆動していない）、(2) モータがエラー状態、(3) 温度が `motor_temp_stop_c`（既定 70℃）以上、のどれかで開始を拒否／保持する。`return-to-start` と `open-grippers` は熱くても実行できる。温度は `temps.csv`（1 Hz）と `summary.json` の `motor_temp_max` に残る
- 温度を見る（何も送らない）：`ssh -t spark '~/molmoact2-setup/g5 temps --seconds 600'`。表示は `ロータ/MOS`［℃］、m2 = 肩、m3 = 肘、m7 = グリッパ。**follower server が止まっている腕の温度は読めない**（モータは指令への返信でしか温度を出さない）
- **熱の運用ルール**（2026-09-30 の過熱トリップから）：
  - 腕を伸ばした姿勢（肩 j2 > 1.8 rad）のまま放置しない。試行が終わったら、または保持で止まったら、**すぐ** `return-to-start`。executor が動いていない間は誰も温度を見ていない
  - start pose の保持だけでも肩は 55℃ 前後まで上がる。試行の合間が長いときは、腕を支えて server を止め、畳んで休ませる（再開は §3.4 → pose-guide）
  - 100 chunk 級の連続 run の後は、同じ時間以上休ませる

✅ 2026-09-30：direction-test / hold / shadow / run（片腕 → 両腕 → 把持物）を実施。8 回の把持試行で「接近 → 閉じる → 持ち上げ → 横へ」の動作は出るが、閉じる位置が対象から数 cm ずれて未把持。

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
| `no motor feedback on can_follower_r ...` で開始拒否／保持 | その腕の follower server がモータを駆動していない。`tail ~/molmoact2-setup/logs/bi_yam_servers.log` を見る。`motor over temperature` なら下の行へ |
| `motor over temperature`（server ログ）／片腕だけ脱力 | モータ自身の過熱保護。server は凍った state を返し続けるので run も return も効かない。**両腕を支えて** server を止め（§3.4）、畳んで冷ます（20〜30 分は目安で、実測値はまだない）→ server 再起動 → `g5 temps` で温度確認 → pose-guide。再起動が進まない（ポート 1234/1235 が開かない）ときはモータがまだエラーを保持している：さらに冷ますか、腕の電源を入れ直す |
| `motor too hot: ...` で保持 | `return-to-start`（熱くても動く）→ 腕を支えて server を止めて休ませる |
| モータの LED | 緑 常灯 = 使能（トルクあり）。赤 **常灯** = 失能（通電直後の既定状態、正常）。赤 **点滅** = 故障コードを保持：3/4/5 校正・センサ異常、8 過電圧、9 低電圧、A 過電流、B MOS 過熱、C コイル過熱、D 通信ロス（400 ms 以内に指令が来なかった。server を止めた後はこれ）、E 過負荷。点滅の回数では区別できず、コードは CAN の返信フレーム（`g5 temps`、server 稼働中のみ）で見る。server 再起動時に i2rt が clear → enable を繰り返すので通常は消える（達妙 DM-J4310/4340 取説 V1.3 の「指示灯状態」） |
| 異常音・発煙 | **即タップ OFF**。再開しない |

## 6. 変更履歴

- 2026-09-30: 初版。右腕の CANable（serial 208E37AE45465006）を `can_follower_r` に割当
- 2026-09-30: 右肩モータの過熱トリップを受けて、モータ監視・`g5 temps`・熱の運用ルール・復旧手順を追記
- 2026-10-01: 硬い物を掴むためグリッパの追従・chunk ガードを無効化、異常保持と `r` でグリッパ目標を維持、保持中の o/r キーの取りこぼしを修正（`configs/dry_run.yaml`、`dryrun/executor.py` を Spark に同期）。実装者向け解説 `docs/yam-setup-and-safety-guide.md` を追加
