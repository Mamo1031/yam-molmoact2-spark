# Gate G4

| 項目 | 値 |
|---|---|
| 日付 | 2026-09-30 |
| 担当 / 監視 | |
| Spark OS / driver / CUDA | Ubuntu 24.04.5 / 580.178.04 / CUDA 13.0、torch 2.11.0+cu130 |
| 使用 commit（client / server / i2rt / official） | e0bf4a54 / 8c6ae2f5 / f3dbf01 / 66b87e6、checkpoint 8dcbed66 |

## 実行したこと
- 2026-09-30 14:3x: **実観測・非駆動試験**（`scripts/dry_run_no_actuation.py --config configs/dry_run.yaml --queries 30 --period 1.0`）
  - 入力：RealSense 3 台（top D435 / left D405 / right D405、640×360@30、LeRobot `RealSenseCamera`）+ 両腕 14D state（follower servers 1234/1235 経由、腕は重力補償で静止、把持物なし）
  - policy：公式 FastAPI server 8202（bf16、CUDA Graph なし）、instruction "Pick up the object and place it on the side."
  - **action は一切送信していない**（`command_joint_pos` を呼ぶコードは無い）

## 結果（貼り付けたログの要約）
- 30/30 クエリ OK。actions **(30, 14) float32**、NaN/Inf 0、閾値違反 0
- 往復 latency：mean 444 / median 444 / p95 450 / max 467 ms（server 側 dt_ms ≈ 432〜459 ms）→ **約 2.25 Hz**
- 現在 state からの初動差：最大 0.026 rad（left_joint_5 / right_joint_2）
- 両 CAN バスエラー 0（試験中 follower servers 稼働）
- 成果物：`logs/g4-real-actions-2026-09-30.png`（14 次元の時系列）、`logs/g4-real-summary-2026-09-30.{json,csv}`、生データ `logs/raw/dry_run_real_01/`

## chunk の内容分析（30 chunk × 30 step × 14）
- state は 30 s 間ほぼ不変（std ≤ 0.0003 rad）＝腕は重力補償で静止
- chunk 末尾（1 s 後の目標）と現在 state の差：平均 |Δ| ≤ 0.02 rad、最大 0.028 rad（left_joint_5）→ **モデルは「ほぼその場に留まる」行動を出している**（把持物が無く、両腕は畳んだ姿勢のため妥当）
- 1 step あたりの最大ジャンプ：≤ 0.016 rad（グリッパ含む）→ 滑らか
- グリッパ action：左右とも 0.984〜1.000（開のまま）
- 最も動く次元（chunk 内 total variation）：right_joint_1 0.085、left_joint_1 0.074、right_joint_2 0.071 rad
- 解釈：分布内の「タスク開始前」画像に対して微小な探索的動きのみ。**G5 で実行するなら、初回は把持物あり・訓練分布に近い配置で改めて非駆動試験を行い、action の大きさを見てから**

## 問題と対応

## 次へ進む条件の充足
- [ ] （reference.md §1 の条件を転記）
