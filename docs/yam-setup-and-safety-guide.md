# YAM × MolmoAct2：初期セットアップとセーフティガードの解説（公式スタックで実装する人向け）

対象：allenai/molmoact2 の公式 YAM 例（`examples/yam`：`launch_yaml_eval_molmoact.py` + `gello_min` + i2rt）を土台に自前で実装する人。
本書は、うちのラボが実機で動かすまでに決めた「初期セットアップで何を・なぜ・どうやるか」と「安全ガードの中身と値」を、
うちのコードを読まなくても再現できるようにまとめたもの。数値は **うちの機体（YAM Standard ×2、DGX Spark、木の机）で
2026-09-30 に動いた値**で、checkpoint `allenai/MolmoAct2-BimanualYAM`（snapshot 8dcbed66）、i2rt f3dbf01、公式 66b87e6 を前提にしている。
机の高さとカメラ露出は環境依存なので必ず測り直すこと。うちは腕のサーバを別プロセス（LeRobot fork の follower server）で動かしたが、
公式は方策と同じプロセスで i2rt を動かす（in-process）。この違いが効く箇所には「公式では」と書いた。

3 行で言うと：

1. **畳んだ姿勢ではなく、学習データの中央値の姿勢（start pose）から始める。** 腕は人が手で構え、最初の指令は「今の実測値」。
2. **方策の目標は毎 tick、前回の指令値から最大 0.01 rad（最初は 0.005）しか動かさない**（腕ごとに共通係数）。範囲外はクランプ、机の下は持ち上げる。
3. **追従誤差・モータ状態と温度・サーバ生存を毎 tick 監視し、異常なら「実測値を一度送って保持」**。400 ms のモータ watchdog は切らない。

関節の呼び方：本書の j1〜j6 = モータ ID 1〜6（j1 根元の旋回、j2 肩、j3 肘、j4〜j6 手首）、グリッパ = モータ 7。
状態・行動ベクトルは 14 次元で **左腕 7（j1..j6, グリッパ）→ 右腕 7** の順。単位は rad、グリッパは 0（閉）〜1（開）。
「保持」の意味は §3.5 に定義がある。

---

## 1. 全体像

```
観測（俯瞰 D435 + 手首 D405 ×2 + 関節 state 14）──► /act ──► action chunk（30 行 × 14）
                                                             │ ① chunk 検査：形・NaN/Inf・初動・step 幅・絶対範囲 → NG なら捨てて再推論
                                                             ▼
   30 Hz の tick ごと（chunk の先頭 20 行を 1 tick 1 行）  desired = chunk[k]
        ② レート制限：前回の指令目標 last_target から、腕ごと共通係数で最大 0.01 rad
        ③ 絶対範囲クランプ（i2rt の関節範囲 − 0.2 rad ∩ 学習範囲）
        ④ 床クランプ（FK の grasp_site が机面を下回る目標は、最小の関節修正で机面まで持ち上げる）
        ⑤ 送信 → last_target 更新
   毎 tick 並走する監視（推論中も）：⑥ 追従誤差 |last_target − 実測| / 実測が範囲外 / モータ状態・温度・CAN 受信なし /
                                     CAN エラー / 推論 > 2 s（state 凍結は動作中のみ、tick > 100 ms は run ループのみ）
   異常 → ⑦ 実測値を 1 回だけ送って保持（PD にためた力を抜く）→ 以後は操作者が start pose へ戻す
```

公式ループ（`run_one_rollout` → `dynamic_smoothing` → `env.step_command_only`）には ①④⑥⑦ が無く、②③ も弱い形
（0.01 rad 刻みのブロッキング補間、i2rt の関節範囲 ± 0.15 rad へのクリップ）しかない（§4）。
30 Hz で 1 tick 1 行なのは、学習データ（30 fps）と公式 yaml の `hz: 30` に合わせたもの。

---

## 2. 初期セットアップ

### 2.0 手順（通電から終了まで）

| # | 手順 | 何のため | 合格基準・実測 |
|---|---|---|---|
| 1 | 電源 OFF で配線。Spark 本体電源・USB ハブ（AC 付き）・LAN。**カメラと CANable はまだ挿さない** | 起動前に挿した USB 機器が 2.0 に固定されるという報告があるため（うちでは再現しなかったが念のため） | — |
| 2 | Spark 起動後、CANable をハブの 5 Gbps ポートへ。黒 4 ピンを腕根元へ、黄 XT30 を 24 V アダプタへ。AC は**スイッチ付き電源タップ**に挿し、腕が畳まれて可動域に何も無いことを見て ON | タップがキットに無い E-stop の代わり（うちの 2026-09-30 の試行はタップ導入前で、停止手段はサーバプロセスの kill だった。タップでの停止リハーサルは未実施） | 通電時に各関節が「コッ」と鳴る |
| 3 | `sudo ip link set can_follower_r up type can bitrate 1000000`（左も）。名前は udev で CANable の serial に固定 | 再起動のたびに必要 | `ip -br link` で UP、ERROR-ACTIVE |
| 4 | 腕を畳んだまま手で支えて、`i2rt/motor_config_tool/ping_motors.py --channel <can>` → `i2rt/robots/motor_chain_robot.py --channel <can> --gripper_type linear_4310 --operation_mode gravity_comp`。**起動時にグリッパが自動校正で開閉する**（1 方向あたり最大 2 s + 0.3 s × 2 方向。その間は kp = kd = 0 で重力補償も無い）ので、指を離し、`Gripper limits auto-detected` が出るまで腕を支える。確認が終わったら腕を支えてこのプロセスを止める：i2rt のスレッドは non-daemon なので Ctrl-C 1 回ではトレースバックが出ても終了しない。もう一度 Ctrl-C か kill し、プロセスが消えたことを確かめてから 6 へ（同じ CAN を 2 プロセスで握らない。終了の約 400 ms 後に watchdog で腕が damping になる） | 配線・モータ・グリッパ校正の確認 | motor 1〜7 が online かつ `error_message='normal'`。重力補償ループ ≥ 100 Hz（実測 右 125〜130、左 156 Hz）。グリッパ校正が通る |
| 5 | **公式コードの改修を済ませておく**（§4 と §5 の段階 0〜1：`start_joints` の自動移動を止める、`_park_robot` を外す、開始時チェック、ガード）。改修前の launcher は起動直後に全ゼロ姿勢へ自動で動く | 無監視の自動移動を無くす | — |
| 6 | 腕のサーバ起動（うちは別プロセス。公式は launcher 起動 = i2rt 起動）。zero-gravity で待機 | state が読める | 14 次元 state が取れる |
| 7 | 推論サーバ `host_server_yam.py --port 8202` | | warmup OK、`/act` が (30,14) を返す（実測 430〜470 ms/回） |
| 8 | **起動後に**カメラを挿す（D435 は Spark 直挿し、D405 ×2 はハブ）。順序 [top, left, right] を入れ替えない | | 3 台の serial が見え、`lsusb -t` が 5000M、3 台同時 30 fps（公式は depth も流すので帯域は倍になる。未確認） |
| 9 | 手首画像の平均輝度を見る。白飛びしていれば手動露出（§2.4。再起動で消えるので毎セッション） | 学習データの手首画像は平均輝度 ≈ 109 | 机が白飛びしない（平均輝度 110 前後） |
| 10 | 机高さの較正（初回、机や取付を変えたとき。§2.2）。グリッパを閉じて指先を机に当てた姿勢で FK | 床クランプの基準 | 左右の FK z を記録 |
| 11 | **両腕を人が手で start pose に合わせる**（§2.1）。各関節の \|実測 − start pose\| を表示し、全関節が許容内で 2 s 続いたら READY。12〜15 の各モードを始める前に毎回行う（戻し動作の直後で許容内なら省略可） | 学習分布の中から始める。最初の指令 = 実測値 | 許容 0.2 rad（グリッパは見ない） |
| 12 | 方向テスト（初回・配線変更後。§2.3） | 関節 ID と符号 | j1・j2・j4〜j6 は符号一致かつ変位 > 指令の 50 %、j3 は符号一致のみ（§2.3） |
| 13 | hold 30 s（§2.3） | PD の定常誤差を測り、追従閾値を裏付ける | 定常誤差 ≤ 0.003 rad |
| 14 | shadow（方策ありで送信だけ無効。何も送らないので腕は支えるか置く） | 推論時間・chunk 検査・tick 時間 | chunk が受理され、遅延 tick（処理が tick 予算 100 ms を超えた tick）0。うちの最大 tick は 14〜26 ms で 33 ms 周期にも収まった |
| 15 | run：把持物なしの片腕 → 両腕は max_step 0.005、把持物ありから 0.01 | | 5 chunk 完走、追従率（誤差/閾値）< 1 |
| 16 | 試行が終わったら**すぐ** start pose へ戻す（max_step を 0.004 に下げた戻しモード） | 伸ばした姿勢は肩が過熱する（§6） | |
| 17 | 終了：start pose へ戻す → **両腕を支える** → プログラム停止（公式ではここで i2rt も止まり腕が脱力する。`_park_robot` が外してあること）→ タップ OFF → Spark 停止 → USB を抜く | 重力補償が切れると腕が落ちる | |

### 2.1 start pose（開始姿勢）

| | j1 | j2 | j3 | j4 | j5 | j6 | グリッパ |
|---|---|---|---|---|---|---|---|
| 左 | −0.073 | 1.449 | 1.283 | −0.802 | 0.113 | −0.222 | 0.733 |
| 右 | 0.082 | 1.542 | 1.252 | −0.682 | −0.129 | 0.192 | 0.697 |

```python
START_POSE = [
    -0.073,
    1.449,
    1.283,
    -0.802,
    0.113,
    -0.222,
    0.733,  # left j1..j6, gripper
    0.082,
    1.542,
    1.252,
    -0.682,
    -0.129,
    0.192,
    0.697,
]  # right j1..j6, gripper
```

- **何か**：checkpoint 同梱 `norm_stats.json`（tag `yam_dual_molmoact2`、`action_stats`、7,600 万サンプル）の **行動の中央値（q50）**。両腕を机の上に構え、グリッパを 7 割開いた姿勢。
- **なぜ畳んだ姿勢ではないか**：公式 yaml の `start_joints` は関節が全ゼロ（畳んだ姿勢。グリッパは左 1.0 = 開、右 0.0 = 閉）。学習データでは j2・j3 の 1 パーセンタイルが 0.004〜0.017 rad で、全ゼロは分布の裾。
  うちの絶対範囲（§3.3）は j2・j3 の下限を 0.2 rad にしているので、畳んだ姿勢からは chunk が全部範囲外で拒否される。中央値の姿勢からは方策が普通の動作（例：1 s で肩 −0.7 rad）を出した。
- **どう合わせるか**：i2rt は既定で zero-gravity モード（kp = 0、重力補償のみ）で待機するので、人が両腕を持って動かせる。各関節の |実測 − start pose| が **許容 0.2 rad** に収まり 2 s 続いたら合格。グリッパは対象外。偏差の表示は自作する（各関節の |実測 − start pose| を 10 Hz 程度で表示）。
  i2rt の README は zero-gravity 待機を危険側（PD 目標なし＋watchdog なしだと一定トルクが出続ける）と書いている。手で構えるためにこれを使う以上、**watchdog は必ず ON**。
- **最初の指令は「今の実測値」**：i2rt は最初の `command_joint_pos` で kp が 0 から [80, 80, 80, 40, 10, 10] に跳ぶ。そこで目標が実測から離れていると腕が跳ねる。
  だから、(1) 実測が絶対範囲内、(2) start pose から許容内、(3) 続けて 2 回読んで（うちは数 ms 間隔）全 14 次元の差が 0.02 rad 以内、を確認してから **2 回目の実測値そのものを最初の指令として送り**、送ったのを見てから人が手を離す。
  (3) は数 ms 間隔なので約 7 rad/s 以上の動き（配線不良などの異常値）しか検出せず、静止確認にはならない。静止確認は READY の 2 s 判定で行う（うちは別プロセスの表示ツール。開始直前に同じプロセスで行うか、2 回の読みの間隔を 0.2 s 程度にすると確実）。
- **熱**：start pose を保持するだけでも肩は 55 ℃前後まで上がる（MuJoCo で計算した肩の重力トルク 5.5 Nm）。合間が長いときはサーバを止めて畳む。

### 2.2 机高さの較正

- **何か**：腕の基準座標系での机面の高さ `table_z`。うちの値は **−0.014 m**（右 −0.012、左 −0.0138 の低い方を 1 つの値にした。右腕には 2 mm 甘い。腕ごとに持ってもよい）。取付が違えば必ず測り直す。
- **なぜ**：方策は小物を掴むとき指先を机面より下に要求することがある。関節範囲だけでは机への押し込みを防げないので、FK で指の間の点の高さを見る（§3.3 床クランプ）。
- **手順**：(1) 腕のサーバ起動後、人がグリッパを閉じて指先パッドを机に当てる。(2) その関節角で FK を解き、`grasp_site`（i2rt の `yam_4310_linear.xml` で link_6 の `pos="0 0 0.1347"`、指の間の 1 点）の z を読む。(3) 左右の低い方を `table_z` にする。何も送らない。
- **限界**：見ているのは指の間の 1 点だけ。手首を傾けた姿勢では指の縁やグリッパ本体が机に当たりうる（未対策）。

```python
import mujoco, numpy as np

# the MJCF i2rt itself loads (meshdir="assets" is relative, so use the path inside the i2rt checkout)
model = mujoco.MjModel.from_xml_path("<i2rt>/i2rt/robot_models/yam/yam_4310_linear.xml")
data = mujoco.MjData(model)
site = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, "grasp_site")


def grasp_xyz(q6):
    data.qpos[:6] = q6  # the six arm joints, radians
    mujoco.mj_kinematics(model, data)
    return np.array(data.site_xpos[site])  # metres, arm base frame
```

### 2.3 初回だけの確認（方策なし → 方策ありで送信なし → 実動作）

| テスト | 何をする | 何のため | 合格基準・うちの実測 |
|---|---|---|---|
| 方向テスト | 片腕ずつ、各関節を +0.05 rad 動かして 0.5 s 置き、戻す（他は固定）。レート制限・監視は本番と同じ（うちの実装は動作中にキーを見ない） | 関節 ID と符号の対応 | j1・j2・j4〜j6 は符号一致かつ変位 > 0.025 rad（指令の 50 %）。**j3 は符号一致のみ**（うちでは 0.0065〜0.013 rad しか動かない。重力負荷か支えの手の抵抗と推定、未確定）。他関節への漏れはうちで ≤ 0.001 rad（観測値、判定には使っていない）。カメラの左右は別途画像で確認する |
| hold | 実測スナップショットを固定目標として 30 s 毎 tick 送り、\|目標 − 実測\| を記録（追従誤差は FAULT にせず記録だけ） | PD の定常誤差を知る。追従閾値（§3.4）の根拠 | 定常誤差 ≤ 0.003 rad、手を離してもその場に保持 |
| shadow | 本番ループを送信だけ無効で回す（最初の指令も送らない）。送っていないので追従誤差と生存監視は無効、温度でも止めない。chunk 検査・モータの状態コードと受信なし・tick 予算は有効 | 推論時間・chunk 拒否・tick 時間 | 推論 460〜470 ms、chunk 受理、遅延 tick 0 |
| run | 片腕（もう片腕は固定）→ 両腕 → 把持物。最初は max_step 0.005 | | 5 chunk 完走、追従率（誤差/閾値）< 1 |

### 2.4 機体側の対処

- **手首カメラの質量**：D405 + ブラケット + ケーブル ≈ 0.10 kg は i2rt の重力補償モデルに入っていない。i2rt が読む `yam_4310_linear.xml` の link_6 に子ボディを足す（付録 B）。**自分の i2rt checkout にも同じ変更が要る**。うちでは追加後に静止時の誤差が ≤ 0.003 → ≤ 0.001 rad に減った。動作中の肘・肩の遅れ（0.05〜0.10 rad）は摩擦・重力負荷による PD の遅れで、追加後も残る。
- **手首 D405 の露出**（2026-09-30 の計測。gate ログには未記録）：うちの LeRobot 経路は color 単独ストリームで、この場合 D405 の自動露出が効かず（露出が約 32 ms に張り付く）机が白飛びした。公式の `gello_min` は depth + color を同時に流すので自動露出が効く見込み（うちの計測では depth を流すと同じ場面で 13.9 / 25.9 ms に下がり白飛び 0 %。ただし平均輝度は 131〜144 で、学習データの ≈ 109 より明るい）。まず手首画像の平均輝度（目標 ≈ 110）を測り、白飛びする・明るすぎるときは、別プロセスで `enable_auto_exposure=0`、`exposure`（うちの照明で 12000 µs）を書く。この設定はカメラの電源が切れると消える（Spark の再起動で消えた）ので毎セッション書く。
  **それ以外の項目（gain、brightness、ホワイトバランス、`load_json`）を書かない**：Spark のカーネル（7.0.0-1019-nvidia）では、ストリームしていないカメラへ標準 UVC コントロールを書くと uvcvideo がデッドロックし、再起動が必要になる（2026-09-30 に発生）。露出と自動露出は librealsense が拡張ユニット（XU）経由で送るので別経路で、うちでは安全だった（同じ経路でも固まったという未確認の報告が 1 件ある）。
- **CAN**：腕ごとに CANable と 24 V を分ける。`reset_all_can.sh` は名前が `can` で始まる全インタフェースを down/up する（もう片腕も止まる）ので、腕が動いている間は使わない。これで `Device or resource busy` が出たら CANable を抜き差しする。

---

## 3. セーフティガード

### 3.1 設計原則

1. **レート制限の基準は「前回の指令目標」、実測ではない。** 実測基準で切ると、指令が常に実測から max_step 以内になり、PD が押し返す力は kp × 0.01 = 0.8 Nm が上限になる。静止摩擦（≈ 3.7 Nm、§3.4）に勝てず動き出せないか、重力モデルの誤差や外力に押されると目標ごと流れる。
2. **腕ごとに 1 つの共通係数で縮める。** 関節ごとに切ると経路の方向が変わる。
3. **追従誤差（指令 − 実測）は FAULT。クリップしない。** 押し込み・接触・サーバ死をここで検出する。
4. **床は FAULT ではなくクランプ。** 止めると把持が毎回終わる（§3.4）。
5. **異常時は「関節の実測値を 1 回送って保持」。グリッパは最後の目標を維持する（握った物を落とさない。2026-10-01）。** それ以上は自動で動かさない。全部離すのは操作者の Enter だけ。400 ms watchdog は ON のまま（i2rt のスレッドが死ねば damping で沈む。切ると最後のトルクが出続ける）。

### 3.2 一覧

| ガード | 判定 | 値 | 違反時 | いつ |
|---|---|---|---|---|
| 開始時のモータ確認 | 受信フレームが来る・全モータ enabled・温度 < 停止値 | 受信なし 0.5 s / 70 ℃ | 開始拒否 | 開始時 |
| 開始時の姿勢 | 実測が絶対範囲内 かつ \|実測 − start pose\| ≤ 許容（グリッパ除く）かつ 2 回の読みの差 ≤ 0.02 rad（グリッパ含む） | 許容 0.2 rad | 開始拒否 | 開始時 |
| 最初の指令 | 2 回目の実測値をそのまま送る | — | — | 開始時 |
| chunk 検査 | 2 次元・14 列・行数 ≥ 実行行数（20。うちの実装には無く、足りないと IndexError になる）・NaN/Inf 無し・初動 \|chunk[0] − 実測\| ≤ 0.3 rad・連続 step ≤ 0.2 rad（グリッパは 1.0 / 1.0 = 無効。2026-10-01 まで 0.5）・30 行全部が絶対範囲内 | 左記 | chunk を捨てて再推論。1 run で累積 3 回（受理でリセットしない）で FAULT | chunk ごと |
| 推論 timeout | `/act` の往復 | 2 s | FAULT | chunk ごと |
| レート制限（関節） | 腕ごとに s = min(1, max_step / max\|Δ\|) | 0.01 rad/tick（初回 0.005、戻しは 0.004） | 縮める | 毎 tick |
| レート制限（グリッパ） | \|Δ\| ≤ max_step_gripper | 0.03/tick | 切る | 毎 tick |
| 絶対範囲（目標） | i2rt 関節範囲 − 0.2 rad ∩ 学習 min/max、グリッパ [0,1] | §3.3 の表 | クランプ | 毎 tick |
| 床クランプ | FK の grasp_site 高さ ≥ table_z + 余裕 | 余裕 0.0 m | 最小の関節修正で机面へ持ち上げ | 毎 tick |
| 追従誤差 | \|last_target − 実測\| ≤ 閾値（次元ごと） | j1 0.08 / j2 0.10 / j3 0.10 / j4–6 0.10 / グリッパ 1.0（= 無効。2026-10-01 まで 0.2） | FAULT | 毎 tick（推論中も。shadow では無効） |
| 絶対範囲（実測） | 実測が目標と同じ範囲内（ヒステリシス無し、グリッパ含む） | 同上 | FAULT | 毎 tick（うちの試行で発火 0 回） |
| start pose からの逸脱 | max \|実測 − start pose\| ≤ 上限 | **不使用**（0.5 rad で入れていたが外した。§3.4） | FAULT | — |
| モータ監視 | 状態コード ≠ enabled / 受信なし 0.5 s / max(ロータ, MOS) ≥ 停止値（警告は 60 ℃） | 70 ℃ | FAULT（戻し動作と shadow は熱で止めない） | 毎 tick |
| 生存監視 | 1 s 窓で関節の目標が 0.02 rad より動いたのに関節の実測が全く変わらない（グリッパは見ない：硬い物で止まったグリッパが誤検知になる） | — | FAULT | execute・戻し動作・方向テストの移動中（関節の目標が動くときのみ。open-grippers では働かず、モータ監視だけが頼り）。shadow では無効 |
| CAN エラー | rx/tx error counter が増えた | — | FAULT | 毎 tick |
| 送信エラー | `command_joint_pos` の例外（公式の in-process では i2rt のスレッドが死んでも例外は出ず指令を保存するだけ。死んだ腕は生存監視・モータ監視で検出する） | — | FAULT | 毎 tick |
| tick 予算 | 1 tick の処理時間 | 100 ms | FAULT | run ループのみ |
| 操作キー | Enter = 保持して全部離す（グリッパも実測値へ）、q = 終了（tick ループ中は随時）。o = グリッパ開、r = start pose へ戻る（保持中のみ。物は握ったまま戻る）。うちの実装では r/o の動作中と方向テスト中はキーが効かない（止めるのはタップか kill） | — | — | — |

### 3.3 式と値

**レート制限と絶対範囲（毎 tick）**

```
Δ = desired[arm] − last_target[arm]            # 6 関節
s = 1                                  if max|Δ| ≤ max_step
  = max_step / max|Δ|                  otherwise
target[arm] = last_target[arm] + s·Δ           # 方向を保ったまま縮める
target[g]   = last_target[g] + clip(desired[g] − last_target[g], ±0.03)
target      = clip(target, lower, upper)
```

絶対範囲 `lower/upper` = `[関節範囲の下限 + 0.2, 上限 − 0.2] ∩ [学習 min, 学習 max]`、グリッパ [0, 1]。表の関節範囲は XML（`yam_4310_linear.xml`）の値（i2rt `get_robot.py` の固定値とは最大 0.0044 rad 違う）。
margin 0.2 の理由：i2rt は指令をその範囲 ± 0.15 にクリップし、**実測がクリップ範囲からさらに 0.1 を超える（= 関節範囲 ± 0.25）と `Joint limit violation` でモータスレッドを止める**。目標を内側 0.2 で止めれば、行き過ぎや外力があっても停止点まで 0.45 rad ある。

| 関節 | 関節範囲（XML） | 学習 min / max（左） | 学習 min / max（右） | **実効範囲 左** | **実効範囲 右** |
|---|---|---|---|---|---|
| j1 | [−2.618, 3.13] | −1.988 / 1.808 | −1.677 / 2.441 | [−1.988, 1.808] | [−1.677, 2.441] |
| j2 | [0, 3.65] | −0.007 / 3.199 | −0.007 / 3.108 | [0.2, 3.199] | [0.2, 3.108] |
| j3 | [0, 3.13] | −0.003 / 3.151 | −0.003 / 3.153 | [0.2, 2.93] | [0.2, 2.93] |
| j4 | [−1.571, 1.571] | −1.696 / 1.593 | −1.706 / 1.665 | [−1.371, 1.371] | [−1.371, 1.371] |
| j5 | [−1.571, 1.571] | −1.573 / 1.589 | −1.629 / 1.595 | [−1.371, 1.371] | [−1.371, 1.371] |
| j6 | [−2.094, 2.094] | −2.184 / 2.208 | −2.143 / 2.164 | [−1.894, 1.894] | [−1.894, 1.894] |

**追従誤差（毎 tick、推論中も）**：`err = |last_target − measured|`、1 つでも閾値を超えたら FAULT。比較対象は方策の生の行動ではなく **実際に送った目標**。閾値 × kp が接触時に許す関節トルクの目安：j1 6.4 Nm、j2・j3 8 Nm、j4 4 Nm、j5・j6 1 Nm。

**chunk 検査（受信ごと）**：初動は chunk[0] と「推論結果を受け取った tick の実測」との差。腕ごとに最悪関節で判定し、グリッパは別閾値。範囲チェックは実行しない 21〜30 行目も含む 30 行全部に掛かる。拒否したら何も送らずに再観測・再推論。拒否は 1 run で累積して数え、3 回目で FAULT（受理してもリセットしない）。
目標の範囲外は ③ でクランプするのに chunk では拒否する、という二重の規則になっている：範囲外の chunk は分布外の兆候とみなしている。うちの缶試行では右 j4 が ±1.371 を超える chunk が 3 回拒否されて 1 run が止まったが、3 回とも範囲外は実行しない 26〜30 行目だけ（超過 0.004〜0.028 rad、学習 q01 −1.424 の内側。右 j4 は学習データの 1 % 以上がクランプ範囲の外）。実行行だけを検査していれば 3 回とも避けられた。「実行する 20 行だけを見る」「わずかな超過はクランプに任せる」は未試験の緩和案。

**床クランプ（毎 tick、レート制限の後）**：`floor = table_z + 余裕`。目標の FK 高さ `z` が floor 未満なら、高さの勾配 `J_z`（6 関節について差分近似、ε = 1e-4）に沿った最小ノルムの修正を最大 6 回かける。

```python
def lift_to(q6, min_z, iterations=6, eps=1e-4):
    """Smallest joint change that raises grasp_site to min_z; None if there is none."""
    q = np.array(q6, dtype=float)
    for _ in range(iterations):
        z = grasp_xyz(q)[2]
        if z >= min_z:
            return q
        grad = np.array([(grasp_xyz(q + eps * e)[2] - z) / eps for e in np.eye(6)])
        norm2 = grad @ grad
        if norm2 < 1e-8:
            return None
        q = q + grad * (min_z - z + 1e-4) / norm2  # aim 0.1 mm above the plane
    return q if grasp_xyz(q)[2] >= min_z else None
```

修正後は絶対範囲（§3.3 の実効範囲）にクリップする。勾配 ≈ 0、6 回後も floor 未満（`lift_to` が None）、またはクリップした結果が floor − 0.1 mm 未満なら、その腕の関節目標は前回の指令のまま（グリッパは通常どおり）。関節空間で高さ勾配に直交する成分を残すので水平方向の動きはほぼ残るが、x・y は拘束していない。持ち上げた tick の変位は max_step を超えうる。

**参考実装（numpy、本質部分のみ）**

```python
import numpy as np

MAX_STEP_JOINT = 0.01  # rad per 30 Hz tick, one common scale per arm
MAX_STEP_GRIPPER = 0.03  # gripper units (0..1) per tick
TRACK_MAX = np.array([0.08, 0.10, 0.10, 0.10, 0.10, 0.10, 1.0] * 2)  # gripper 1.0 = off
LOWER, UPPER = ...  # 14 absolute bounds, see the table in section 3.3
ARMS = (slice(0, 6), slice(7, 13))
GRIPPERS = (6, 13)


def rate_limit(desired, last_target):
    """Move from the LAST COMMANDED target toward desired, at most one step per tick."""
    target = last_target.copy()
    for arm in ARMS:
        delta = desired[arm] - last_target[arm]
        worst = np.max(np.abs(delta))
        s = 1.0 if worst <= MAX_STEP_JOINT else MAX_STEP_JOINT / worst
        target[arm] = last_target[arm] + s * delta  # one scale keeps the direction
    for g in GRIPPERS:
        d = np.clip(desired[g] - last_target[g], -MAX_STEP_GRIPPER, MAX_STEP_GRIPPER)
        target[g] = last_target[g] + d
    return np.clip(target, LOWER, UPPER)


def fault_reason(measured, last_target):
    """Tracking error is a FAULT condition, never something to clip."""
    if np.any(np.abs(last_target - measured) > TRACK_MAX):
        return "tracking error"
    if np.any((measured < LOWER) | (measured > UPPER)):
        return "measured pose outside bounds"
    return None


# --- control loop (sketch) ---
measured = read_measured()  # arms held by hand at the start pose, checked first
send(measured)  # the first command equals the measured pose (kp 0 -> 80)
last_target = measured.copy()
for desired in chunk[:20]:  # one row per 30 Hz tick
    measured = read_measured()
    reason = fault_reason(measured, last_target) or motor_monitor_reason()
    if reason:
        hold = measured.copy()
        hold[list(GRIPPERS)] = last_target[list(GRIPPERS)]  # keep the grip on a fault
        send(hold)  # release the stored PD force of the joints once ...
        break  # ... then hold and wait for the operator (section 3.5)
    target = keep_above_table(rate_limit(desired, last_target))  # lift_to() per arm, section 3.3
    send(target)
    last_target = target
    sleep_until_next_tick()  # on a late tick reset the schedule (section 3.6)
```

### 3.4 値の根拠（1 行ずつ）

- **max_step 0.005 → 0.01 rad/tick**：0.01 は公式 `dynamic_smoothing` の補間刻みとほぼ同じ（30 Hz で 0.3 rad/s）。shadow の解析では 0.005 で係数の中央値が 0.22 / 0.39（方策の要求速度の 3〜5 倍遅い）、実動作の両腕 run では 0.73 / 0.64。0.01 でも右腕の係数中央値は試行により 0.12〜0.49、速い局面（下位 10 %）は 0.04〜0.12（8〜25 倍遅い）。把持物なしの片腕・両腕 run は 0.005、把持物ありから 0.01 にした。0.02 は未試験（0.01 の戻し動作で肩が 0.10 rad 遅れた例があるので、上げるなら追従閾値と併せて見直す）。
- **戻し動作は 0.004 rad/tick**：0.01 だと伸ばした姿勢から持ち上げるときに肩が 0.10 rad 遅れて FAULT した。戻し動作は必ず max_step を 0.004 に下げて実行する（うちの r キーは run の max_step のまま戻るので、戻しは別モードで行っている）。
- **追従閾値 j1 0.08 / j2 0.10 / j3 0.10**（当初 0.05）：肘は重力負荷下で動き出す前に 0.046 rad ためる（kp 80 × 0.046 ≈ 3.7 Nm。静止摩擦と推定）。肩は 0.2 rad/s で持ち上げるとき 0.06 rad 遅れた（接触ではない）。静止時の誤差は 0.001 rad 以内なので、重力モデルの問題ではない。j1 は観測なし、念のため 0.08 に揃えた。
  **グリッパ 0.2 → 1.0（= 無効、2026-10-01）**：硬い物を掴むとグリッパは物の幅で止まるが方策は「閉」を出し続けるので、誤差が数 tick で 0.2 を超えて FAULT し、異常時処理の `send(measured)` で把持力まで抜けていた。把持力の上限はうちのガードではなく i2rt 側の把持力制限（`limit_gripper_force` 50 N）が担う。グリッパを空で動かす確認では 0.2 に戻してよい。あわせて、腕側の FAULT で保持に入ってもグリッパは最後の目標を維持するようにした（以前は `send(measured)` で握っていた物が離れた）。全部離すのは Enter。別プロセスで起動する戻し・開モードは最初の指令が実測値なので、その時点で物は離れる（先に置くこと）。
- **chunk の初動 0.1 → 0.3、step 0.1 → 0.2 rad**：レート制限で腕が計画に遅れるため、再計画した chunk の初動が実測から 0.1〜0.15 rad 離れるのは正常。0.1 rad/step は方策の普通の速さ。実動作の安全はレート制限が担保するので、ここは「左右や関節順を取り違えた state（chunk[0] が実測から大きく離れる）やゴミ」だけを弾く。グリッパは 0.109 の正常なジャンプが弾かれたため 0.5 に分離し、2026-10-01 に 1.0（無効）へ：物で止まったグリッパの実測と「閉」の指令の差は 0.9 近くになりうる。
- **start pose からの逸脱 0.5 rad → 不使用**：箱へ手を伸ばす正常な動作（右肩が start pose から約 0.5 rad、学習 q99 以内）で止まった。絶対範囲・学習範囲・床クランプが残る。
- **床：余裕 3 cm → 1 cm → 0、FAULT → クランプ**：指先を机に当てて較正したので grasp_site の高さ = 指先の高さ。余裕 3 cm では指先が机上 2.8 cm で止まった（この箱は開口より広く、どのみち上から掴めなかった）。1 cm でもボール把持は 5 回中 4 回止まった（方策は閉じる局面で指先を机上 0〜2 cm に置き、判定は指令目標なので下降中は実測より 0.3〜1.4 cm 先行する）。0 でも止まったので、止めずに持ち上げる方式にした。
- **温度 警告 60 / 停止 70 ℃**：右肩（DM4340）が伸ばした姿勢（MuJoCo 計算の重力トルク 9.6 Nm、定格連続 9 Nm）に run 中の約 2 分 + 停止後の約 2 分、計約 4 分いて、自身の過熱保護でトリップ（状態コード 0xC）。モータのトリップ温度は未計測（ベンダ推奨はコイル 100 ℃以下）。start pose 保持だけで 55 ℃に達するので、70 は推定値。実運用の温度ログを見て調整する。
- **受信なし 0.5 s = FAULT**：モータは指令への返信としてしか温度を返さない。受信が無い = 誰も駆動していない。
- **tick 予算 100 ms**：chunk ごとの画像保存（3 PNG ≈ 100 ms）を制御スレッドで走らせて 115 ms になり保持した。重い I/O は別スレッドへ。
- **推論 timeout 2 s**：`/act` は 430〜470 ms。
- **chunk 30 行のうち 20 行を実行**：公式は 25。20 を選んだ理由は特に無く、25 は未試験。止まる長さ（推論 ≈ 0.47 s）は同じで、止まる頻度と開ループで走る長さ（0.67 s vs 0.83 s）が変わる。

### 3.5 「保持」の定義と異常時の動作

- 腕を保持しているのは **i2rt のモータスレッド**：最後に受けた目標を kp [80, 80, 80, 40, 10, 10] / kd + 重力補償で 100 Hz 以上で追い続ける。400 ms watchdog もこのスレッドの周期送信で満たされる。だから方策ループは推論中（≈ 0.47 s）も保持中も送らなくてよい。
- **異常保持**：関節は実測を 1 回送り（PD にためた力を抜く）、グリッパは最後の目標のまま（握った物は落とさない）、以後は送らない。腕はその場に止まる。
- **hold テスト**：同じ目標を毎 tick 送り、追従誤差は FAULT にせず記録だけする。
- **Enter**：異常保持と同じだが、グリッパも実測値にして全部離す（一発で力を抜くキー）。
- **公式（in-process）での注意**：モータスレッドは方策と同じプロセスにいる。**保持中にプロセスを終了させない**（`_park_robot` を外していなければ終了処理で先に腕が `start_joints` へ動き、プロセスが抜けた約 400 ms 後に damping で沈む。終了の挙動は §4 落とし穴 3）。異常保持後は操作者が r（戻し）を押すか、腕を支えてから q を押すまでプロセスを生かしておく。うちの別プロセス構成では、方策プロセスが抜けてもサーバが姿勢を保つが、伸ばした姿勢のまま誰も温度を見ていない状態になるので、抜けたら直ちに戻し動作を行う。
- **戻し動作（r / 戻しモード）**：desired = START_POSE（グリッパは最後の目標のまま。物は握ったまま戻り、開くのは o）、max_step 0.004、グリッパ 0.03/tick、追従誤差・床クランプは有効、温度による FAULT は無視、目標が START_POSE に達したら終了（60 s で timeout）。途中で止まったらもう一度。動作中もキーを poll して Enter で異常保持にする。実測が範囲外で止まった後は、範囲判定を有効にしたままだと戻しが 1 tick 目で再 FAULT する（うちの実装は開始も拒否）ので、戻しでは実測の範囲判定を外すか広げる、または人が支えて範囲内へ戻す。
- **o**：全グリッパの目標を 1.0 へ 0.03/tick で。
- **q**：ループを抜ける（何も送らない。公式では上の注意どおり、腕を支えてから）。
- キー入力は 1 tick に 1 回だけ poll して状態で分岐させる（うちの実装も 2026-10-01 にそうした。戻し・開動作中はまだキーを見ない）。別プロセスで起動する戻し・開モードは最初の指令が実測値なので、握っていた物はその時点で離れる。

### 3.6 制御ループの形

- 30 Hz 固定周期（単調時計）。うちの実装は遅れた tick の後に sleep を飛ばして予定時刻へ追いつくので、遅延の直後に 0.01 rad の step が数 tick 分まとめて出うる。追いつかせないなら遅延時に `t_next = now + dt` にリセットする。推論は worker スレッドで、その間もメインループは監視とキー入力を回す。
- chunk 30 行のうち先頭 20 行を実行 → 再観測（カメラ 3 台 → 最後に state）→ 再推論。1 サイクル ≈ 0.67 s 動作 + 0.47 s 推論。ブレンドなし。新 chunk は旧計画を置き換え、`last_target` はリセットしないので目標は連続。
- 方策に渡す state は **実測**（うちは全試行これ）。学習データの state が実測か指令値かは未確認。
- 記録：tick ごとに方策の生行動・送った目標・実測・レート制限係数・tick 時間、chunk ごとに state・行動・拒否理由、1 Hz でモータ温度、run ごとに床クランプした tick 数（tick ごとのフラグも残すと良い）。異常時に見るのはこれだけ。

---

## 4. 公式スタックへの組み込み

公式ループで腕に行く指令は `RobotEnv.step_command_only` → `BimanualRobot.command_joint_state` → `YAMRobot.command_joint_state` → `command_joint_pos` → i2rt を通る（start pose 移動と park も同じ経路）。
毎回の指令に掛けるガード（レート制限・クランプ・床・追従）はこの経路に置くが、**`YAMRobot.command_joint_state` より上（`step_command_only` か `BimanualRobot.command_joint_state`）に置く**：`YAMRobot.command_joint_state` は受け取った値をそのままキャッシュ `_joint_state` に書き、それが方策の state と補間の起点になるため、下で変えると「送った値」と食い違う。
max_step は 30 Hz で呼ぶ前提の値なので、`dynamic_smoothing` は残さず、自前の 30 Hz ループ（1 tick 1 行）に置き換える。chunk 検査と timeout は推論直後に別途入れる。i2rt のグリッパ自動校正はこの経路を通らない。

| ラボのガード | 公式のフック位置 | 公式の現状 | 注意 |
|---|---|---|---|
| start pose 確認 + 最初の指令 = 実測 | `main()` の `move_to_start_position(...)` の前、rollout ごとの同呼び出しの前 | 確認なし。`start_joints` へ即移動 | 実測は左右それぞれの `YAMRobot.get_joint_pos()` を連結する（`BimanualRobot` には無い）。`get_observations()` は**指令値のキャッシュ**を返す。`get_joint_state()` も実測を返すがキャッシュを書き換えるので使わない |
| レート制限・絶対クランプ・床クランプ | `dynamic_smoothing()` を自前の 30 Hz ループに置き換え、`step_command_only` の直前で掛ける | 線形補間 `steps = min(int(max_delta/0.01), 100)` のブロッキング移動。刻みは max_delta/(steps−1) で常に 0.01 より少し粗く、0.03 rad 未満の変位は 1 tick で送られ、1 rad 超では max_delta/99。範囲は i2rt の関節範囲 ± 0.15 へのクリップのみ。グリッパは `command_joint_pos` ではクリップされないが、i2rt の制御ループで毎周期 [0,1] にクリップされ把持力制限（50 N）もかかる | うちは「1 tick 1 行、前回目標基準」 |
| chunk 検査・推論 timeout | `policy.inference(...)` の直後（`MolmoAct.inference`） | 明示的な検査なし：行長は assert、NaN/Inf は `dynamic_smoothing` の `int()` で例外になりループが落ちる（→ 終了処理へ）、行数不足は IndexError。`requests.post` に timeout なし | 拒否時は再推論。timeout を付ける |
| 追従誤差・生存監視 | 送信のたびに実測を読む（`get_joint_pos()`） | 実測を読まないので検出不能 | 腕のスレッドが死ぬと state が凍り指令は黙って無視される（§6） |
| モータ状態・温度 | 公式とは独立に、受信専用 SocketCAN（付録 C） | なし（`motor_chain.read_states()` で温度は読めるが、制御スレッドが生きている間しか更新されない。状態コードは常に 0x1：異常フレームを受けた時点で i2rt の制御スレッドが例外で止まり、その値は state に入らない。標準出力に `DM Error in control loop: Motor error detected: ...` が出る） | 状態コードと受信なしの判定には受信専用ソケットが要る |
| 異常時の保持・戻し | `_park_robot`（`atexit` 登録）を外し、自分の保持処理と各腕の `YAMRobot.robot.close()`（i2rt `MotorChainRobot`。スレッドを止めるだけでモータは off にしないので約 400 ms 後に watchdog で damping）に置き換える | 終了時に無監視で `start_joints` へ線形補間で移動（最大 100 tick ≈ 3.3 s。1 rad 以下なら約 0.3 rad/s、伸ばした姿勢からだと 0.5〜0.6 rad/s）。i2rt のスレッドが死んでいれば無視される | 異常後の自動移動が一番危ない。終了の挙動は落とし穴 3 |

**落とし穴**

1. **方策に渡す state が指令値**（`YAMRobot._joint_state` は最後の `command_joint_state` の値）。推奨は実測（`get_joint_pos()`）：うちは全試行これで動かした。指令値を渡すと、レート制限で腕が遅れている分だけ方策が実際より先の姿勢を前提にする。追従判定は必ず実測で。
2. **`start_joints` は関節が全ゼロ（畳んだ姿勢）、グリッパは左 1.0（開）・右 0.0（閉）**。範囲外・分布の裾（§2.1）。毎 rollout の開始時に左は開き右は閉じる。
3. **終了の挙動**：i2rt のスレッドは non-daemon で launcher は `close()` を呼ばないため、Ctrl-C や例外のあと、そして全 rollout を終えた正常終了でも、**プロセスは終了せず、腕は最後の目標を保持したまま止まる**。そこで操作者が Ctrl-C を押した時点（Ctrl-C で止めた場合は 2 回目、正常終了なら 1 回目）で `atexit` の `_park_robot` が走り腕が動き出し、その後プロセスが抜けて約 400 ms 後に damping で沈む。`_park_robot` を外し、終了処理で明示的に保持してから各腕の `YAMRobot.robot.close()` を呼ぶ（腕を支えてから）。
4. **HTTP timeout がない**。サーバが固まると腕は最後の目標で止まり続ける（それ自体は安全）が、ループは永遠に待つ。
5. **`dynamic_smoothing` はブロッキング**（1 行あたり最大 100 tick ≈ 3.3 s。移動中は監視もキー入力も効かない）。`move_to_start_position` は `steps` が 0 か 1（残差 < 0.02 rad）だと何も送らず、残差が残る。
6. **i2rt の致命チェック**：実測がクリップ範囲 ± 0.1（= 関節範囲 ± 0.25）を超えると `Joint limit violation`、モータスレッド停止 → state 凍結。だから目標の範囲は内側 0.2 で切る。
7. **`set_timeout.py` で watchdog を切らない**（公式 README は長時間セッションのために切れと書くが、スレッドが死んだとき最後のトルクが出続ける）。このツールは引数なしで 0（無効）を書き、`--timeout` で 8000（単位 50 µs = 工場出荷の 400 ms）に戻す。どちらも各モータの書き込み直前にそのモータを motor_off する（既定は 1〜7 全部なので順に全関節が脱力する）。値を読むだけなら `motor_config_tool/utils.get_special_message_response(can, id, "timeout")`。
8. **起動時にグリッパが自動校正で開閉する**（LINEAR_4310、1 方向あたり最大 2 s + 0.3 s × 2 方向、kp = kd = 0 で重力補償なし。左腕 → 右腕の順）。畳んだ姿勢か手で支えて待つ。公式 launcher では i2rt の INFO ログ（`Gripper limits auto-detected`）は既定で出ない（i2rt が import 時に root logger を ERROR にする）。腕ごとに `initializing motorchain robot ...` が出てから校正が始まり、両腕の初期化が終わると `Launching robot:` が出るので、それまで支える（`LOGLEVEL=INFO` を付ければ INFO ログも出る）。手を挟まない。
9. **CAN 名**：公式 yaml は `can_left` / `can_right`、うちの udev は `can_follower_l` / `can_follower_r`。
10. **公式は chunk 30 行のうち 25 行を実行**（`action_horizon = 25`）。うちは 20。
11. **グリッパの入出力**は 0 = 閉、1 = 開。学習データもこの向き。
12. **zero-gravity 待機**は i2rt README が危険側と明記（§2.1）。watchdog を ON にしたまま使う。

---

## 5. 最小ガードセット（段階的に入れるなら）

- **段階 0（コードはほぼ無し）**：スイッチ付き電源タップと停止リハーサル、腕の下に緩衝材、2 人体制（操作と停止）、`start_joints` を **null** にして自動移動を止める（`move_to_start_position` は None なら何もしない。§2.1 の値を入れるのは段階 1 の開始時チェックを入れてから）、`_park_robot` を外す、watchdog は ON のまま、手首カメラの質量を XML に追加。タップが無いうちの停止手段はプロセスの kill（watchdog で damping）。
- **段階 1（最初に方策で動かす前）**：start pose を手で合わせ、最初の指令 = 実測／レート制限 0.005〜0.01 rad/tick（前回目標基準）／絶対範囲クランプ + 実測が範囲外なら FAULT／chunk の形・NaN・範囲拒否／推論 timeout 2 s／追従誤差 FAULT（この段階ではグリッパも 0.2 のまま。1.0 にしてよいのは段階 2 のモータ監視が入ってから）／異常時は実測を 1 回送って保持（プロセスは生かす）／Enter と q。
- **段階 2（把持物あり・2 分（約 100 chunk）を超える run の前）**：モータ監視（状態・受信なし・温度。静止中や開始時に凍ったサーバを検出でき、原因まで分かるのはこれだけ。動作中は追従誤差・生存監視でも止まる）／床クランプと机較正／生存監視／0.004 rad/tick の戻し動作。
- **段階 3**：CAN エラーカウンタ、tick 予算、tick ごとのログ、o/r キー。

---

## 6. 運用ルール

- 2 人以上。1 人は電源タップに手を添える。Ctrl-C は非常停止ではない。
- 止める前に必ず腕を支える（プロセス停止・サーバ停止・タップ OFF のどれも腕が落ちる）。
- 肩 j2 > 1.8 rad の伸ばした姿勢で放置しない。試行が終わったら、保持で止まったら、すぐ start pose へ戻す。方策ループが動いていない間は誰も温度を見ていない。2 分（約 100 chunk）を超える run の後は同じ時間以上休ませる。合間が長ければサーバを止めて畳む。
- 過熱でトリップしたら：i2rt は凍った state を返し続け、run も戻しも効かない。両腕を支えてプロセスを止め、畳んで冷まし（20〜30 分は目安で実測なし）、再起動して温度を見てから start pose へ。再起動時に i2rt の初期化が進まない（i2rt はモータのエラーを clear → 再 enable するループを黙って回り続け、エラーが消えないと先へ進まない）なら、さらに冷ますか腕の電源を入れ直す。冷却だけでエラーが解除できるかは未確認。
- 異常音・発煙は即タップ OFF。再開しない。
- USB が 480M で認識されたら挿し直す。ハブごと消えたらハブの USB-C を挿し直す（うちで 1 回発生）。
- シーンの教訓：平置きの箱はグリッパの開口より広く上から掴めない。机中央寄りに置いた対象は手首カメラの端にしか映らず、方策が接近をためらった。俯瞰カメラは前方へ傾けた。指示文は小文字（例 `pick up the box and place it on the side`）。未検証の対策：縦置きや細い物を使う、対象を腕の正面・前方 30 cm 程度に置く、学習データに無い透明アクリル板を外す。
- 凍ったサーバは、関節の目標が動いていれば追従誤差・生存監視で止まるが、グリッパだけが動いている間（open-grippers など）はモータ監視（受信なし・状態コード）だけが検出する。グリッパの追従閾値を 1.0 にした以上、モータ監視なしで運用しない。

---

## 付録

### A. 数値一覧

- start pose：§2.1。実効範囲：§3.3 の表。
- i2rt の PD ゲイン：kp [80, 80, 80, 40, 10, 10]、kd [5, 5, 5, 1.5, 1.5, 1.5]、グリッパ kp 20 / kd 0.5（i2rt `robots/utils.py`、LINEAR_4310）、重力補償係数 1.3。
- 学習 q01 / q99：左 j1 −0.66 / 0.47、j2 0.004 / 2.244、j3 0.014 / 2.008、j4 −1.374 / 0.134、j5 −0.359 / 0.883、j6 −0.93 / 0.334、グリッパ 0.051 / 0.987。右 j1 −0.494 / 0.738、j2 0.005 / 2.285、j3 0.017 / 2.061、j4 −1.424 / 0.24、j5 −0.974 / 0.53、j6 −0.472 / 0.962、グリッパ 0.033 / 0.995。
- 周期 30 Hz、chunk 30 行、実行 20 行、tick 予算 0.1 s、推論 timeout 2 s、生存監視 1 s 窓 / 0.02 rad、開始時の動き 0.02 rad、chunk 拒否上限 3 回（1 run 累積）、戻し動作の timeout 60 s。
- うちの机：table_z −0.014 m。カメラ serial：top 138422071505 / left 260322279321 / right 260522272252（同じ機体を使うときだけ）。

### B. 手首カメラ質量（`yam_4310_linear.xml`、link_6 の body 内に追加）

```xml
<!-- Lab addition 2026-09-30: Intel RealSense D405 (60 g) + bracket + cable on the wrist,
     approximated as a point mass 0.10 kg above the gripper base (lateral offset is a guess;
     it only affects the small wrist-roll term). Needed for gravity compensation. -->
<body name="wrist_camera" pos="0 -0.04 0.06">
  <inertial pos="0 0 0" mass="0.10" diaginertia="2e-5 2e-5 2e-5"/>
</body>
```

重力補償は MuJoCo の逆動力学なので、子ボディで正しく反映される。反映後の hold 10 s は定常誤差 ≤ 0.001 rad。

### C. モータ監視のフレーム形式（DM モータ）

| 項目 | 内容 |
|---|---|
| ソケット | 腕ごとに raw SocketCAN をもう 1 本、受信専用（送信しない）。フィルタで ID 0x10〜0x17 の標準データフレームだけ受ける（サーバの指令 0x01〜0x07 はループバックで戻ってくるので除外） |
| ID | `0x10 + モータ ID`（1〜7） |
| data[0] | **上位ニブル = 状態コード**（0x0 disabled、0x1 enabled（i2rt の名前は normal）、0x8 過電圧、0x9 低電圧、0xA 過電流、0xB MOS 過熱、0xC ロータ過熱、0xD 通信断、0xE 過負荷）。下位ニブル = モータ ID は DM のプロトコル仕様（i2rt はこれを使わず CAN ID からモータ ID を取る） |
| data[6] / data[7] | MOS 温度 / ロータ温度 [℃] |
| 判定 | 状態 ≠ enabled → FAULT。最新フレームが 0.5 s より古い → FAULT（誰も駆動していない）。max(ロータ, MOS) ≥ 70 ℃ → FAULT（60 ℃で警告） |

モータは指令への返信としてしか温度を返さないので、プロセスを止めた腕の温度は読めない。公式（in-process）なら `motor_chain.read_states()` でも温度が読めるが、制御スレッドが生きている間しか更新されず、状態コードは常に 0x1（異常フレームを受けた時点でスレッドが止まる）。

### D. 未解決

- 把持は「接近 → 閉じる → 持ち上げ → 横へ」まで出るが、閉じる位置が対象から数 cm（高さ方向は 8 cm 以上の例も）ずれる。疑っているもの：カメラ外部パラメータ・関節ゼロ点・机高さの学習環境との差、手首カメラの白飛び（一部の試行）。もう 1 つの候補（未試験）：腕とグリッパのレート制限が別係数なので、腕だけが計画より遅れ（0.01 でも速い局面は 8〜25 倍遅い）、グリッパが先に閉じうる（腕の係数 s をグリッパにも掛ける案）。
- グリッパの追従閾値と chunk 閾値は 2026-10-01 に無効化した（§3.4）。硬い物を実際に掴んだ試行はまだ無く、i2rt の把持力制限（50 N）だけで足りるか、物の重さで腕の追従閾値（j2 0.10 rad ≈ 8 Nm）に当たらないか、方策が止まったグリッパの state を見て「閉」を出し続けるか（state をそのまま返すと再計画のたびに力が抜ける）は未確認。最初の試行では実物を握った hold テストで各関節の定常誤差と `temps.csv` の m7（グリッパ）温度を見る。
- モータの過熱しきい値（OT_Value）とトリップ時の実温度は未計測。冷却だけでエラーが解除できるかも未確認。
- 戻し動作が約 15 tick で途中終了した例が 1 件、原因不明。
- 400 ms watchdog レジスタの現在値は読んでいない（既定のまま運用。切った場合の挙動も未試験）。
- 公式の depth + color ストリームで手首カメラが白飛びするかどうかは未確認。
