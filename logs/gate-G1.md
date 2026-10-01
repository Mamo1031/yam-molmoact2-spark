# Gate G1

| 項目 | 値 |
|---|---|
| 日付 | 2026-09-30 |
| 担当 / 監視 | |
| Spark OS / driver / CUDA | Ubuntu 24.04.5 LTS (DGX OS), kernel 7.0.0-1019-nvidia, aarch64, GB10, driver 580.178.04, CUDA 13.0, RAM 121 GiB, NVMe 3.7 TB (空き 3.5 TB) |
| 使用 commit（client / server / i2rt / official） | |

## 実行したこと
- 2026-09-30: Mac → `ssh spark`（murata-lab@spark-cf69.local、鍵認証）成立。以後 Claude Code が直接実行
- 2026-09-30: システム情報を `logs/raw/system-info-2026-09-30.txt` に保存（Spark 側 `~/molmoact2-setup/logs/system-info.txt`）
- 2026-09-30: `gs_usb` / `slcan` カーネルモジュールあり（`/lib/modules/7.0.0-1019-nvidia/kernel/drivers/net/can/usb/gs_usb.ko.zst`）→ reference.md §6.1 の「未確認」を解消
- 2026-09-30: USB root hub は 20000M/x2（USB 3.2 Gen2x2）×6。Anker ハブは Bus 005（480M 側）と Bus 006（5000M 側）に見えており、キーボード・マウスは Bus 005 経由
- 2026-09-30: uv 導入、lerobot-client / lerobot-server / molmoact2-official を `~/molmoact2-setup/workspaces/` に clone 開始（sudo 不要分を先行）

## 結果（貼り付けたログの要約）
- 2026-09-30 01:54: CANable #1（左用）を Anker ハブ 5 Gbps ポートに USB 接続 → `lsusb` に **1d50:606f OpenMoko Geschwister Schneider CAN adapter**、udev 属性 **product "CANable 2.5 Candlelight"（ElmueSoft）、serial `208E37AE45465006`**、ドライバ gs_usb、`can0` 生成（DOWN、clock 160 MHz、CAN FD 対応）。ハブ配下 3 段目、12M（Full Speed）
- 注意：CANable を挿した直後にハブごと USB から消えた。ハブの USB-C を挿し直して復帰（原因未特定。以後、ハブの抜き差しは慎重に）
- 2026-09-30 02:0x: `setup_can_left.sh` 実行 → `/etc/udev/rules.d/90-can.rules` に serial→`can_follower_l`、インターフェース **`can_follower_l` UP / bitrate 1000000 / sample-point 0.750 / ERROR-ACTIVE**。`/etc/sudoers.d/can`（`ip link *`, `udevadm *` のみ NOPASSWD）有効
- 2026-09-30: 方針変更「右腕から先に」。serial 208E37AE45465006 の CANable を `can_follower_r` に付け替え（`rename_can_right.sh`）。UP / 1 Mbps / ERROR-ACTIVE。基板に「R」ラベル
- 2026-09-30: 右腕 通電 → `candump` 無通信・エラー 0（DM モータは polling まで沈黙）。`ping_motors.py --channel can_follower_r` → **motor 1〜7 全て online、error 'normal'**、MOS/rotor 23/22 ℃、位置 ≈ 0 rad（motor4 −0.07、motor6 −0.03、motor7(gripper) −0.05）。バス RX 57 / TX 31 フレーム、エラー 0
- 2026-09-30: 右腕 重力補償テスト（`motor_chain_robot.py --channel can_follower_r --gripper_type linear_4310 --operation_mode gravity_comp`、`gravcomp.sh` 経由）→ 7 モータ ON、グリッパ自動キャリブレーション成功（limits 0.080 / −5.053 rad、direction 1）、**Grav Comp 125〜130 Hz**（警告閾値 100 Hz 以上）、CAN 244 it/s、mean step 4.1 ms、>7 ms が 30 s で 17 回、バスエラー 0。CAN が Anker ハブ 3 段目の Full Speed（12M）配下でもこの周期は出た
- 2026-09-30: 重力補償を約 40 s 稼働後に SIGINT で停止（末尾は 156〜163 Hz）。プロセス正常終了、バスエラー 0（累計 RX 29.6 万フレーム）
- 2026-09-30: `read_obs_once.py can_follower_r` → `joint_pos`(6) = [0.108, 0.621, 0.839, 0.536, 0.066, −0.202]（手で支えて持ち上げた姿勢）、`gripper_pos`(1) = 0.9987（開、0〜1 正規化）、`joint_vel`(7)、`joint_eff`(7)（**この commit では vel/eff は 7 要素でグリッパ込み。`gripper_vel`/`gripper_eff` キーは無い**）。正常終了、バスエラー 0
- **右腕 G1 完了（2026-09-30）**：CAN 認識 → 固定名 → ping → 重力補償 → 観測取得
- 2026-09-30 再起動後：左 CANable（serial `20A7378545465006`）が `can0` として出現 → `ip link set can0 name can_follower_l`（今回限り）で改名し 1 Mbps up。恒久化は `setup_can_left_udev.sh`（要 sudo）
- 2026-09-30: 左腕 通電 → `ping_motors.py --channel can_follower_l` → **motor 1〜7 全て online、error normal**、24〜25 ℃。gripper (id 7) pos −1.97 rad（半閉）。バスエラー 0
- 2026-09-30: 左腕 重力補償（`gravcomp.sh start can_follower_l`）→ グリッパ自動校正 OK（limits 0.167 / −5.035 rad）、**Grav Comp 155.6 Hz**、CAN 231.7 it/s、バスエラー 0（右腕は同時接続のまま、`can_follower_r` UP）
- 2026-09-30: 左 重力補償を約 40 s 稼働後に停止（末尾 162.6 Hz）、正常終了、バスエラー 0（累計 RX 20.3 万フレーム）。左 udev ルール登録済み（`setup_can_left_udev.sh`）
- 2026-09-30: `read_obs_once.py can_follower_l` → `joint_pos`(6) = [0.035, 0.010, 0.101, −0.333, −0.001, −0.023]、`gripper_pos` 0.9989（開）、vel/eff 7 要素。バスエラー 0。**左腕 単体 G1 完了**
- 2026-09-30: 両腕同時：`python -m lerobot.scripts.setup_bi_yam_servers --eval` → port 1234（右）/ 1235（左）で待ち受け。`YamArmClient` で両腕 `get_observations()` → **14D state = left(7) + right(7)**、1 組 2.9 ms（342 Hz）、両 CAN バスエラー 0、dropped 0。gripper は両方 0.998（開）
- **G1 完了（2026-09-30、左右）**
- udev 固定名スクリプト `~/molmoact2-setup/setup_can_left.sh` と sudoers 断片 `~/molmoact2-setup/sudoers-can` を Spark に配置（ユーザーが sudo 実行）

## 問題と対応

## 次へ進む条件の充足
- [x] 右：CAN 通信・state 取得が成功（2026-09-30）
- [x] 左：CAN 通信・state 取得が成功（2026-09-30）
- [x] 両腕同時接続で CAN error なし（2026-09-30、follower servers 経由）
