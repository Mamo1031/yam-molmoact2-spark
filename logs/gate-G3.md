# Gate G3

| 項目 | 値 |
|---|---|
| 日付 | 2026-09-30 |
| 担当 / 監視 | |
| Spark OS / driver / CUDA | Ubuntu 24.04.5, kernel 7.0.0-1019-nvidia, driver 580.178.04, CUDA 13.0, GB10 (cc 12.1) |
| 使用 commit（client / server / i2rt / official） | e0bf4a54 / 8c6ae2f5 / f3dbf01 / 66b87e6。checkpoint snapshot 8dcbed66（21 GB） |

## 実行したこと
- 2026-09-30: `uv 0.12.20` 導入。3 環境を並行構築
  - **lerobot-client**（Python 3.10.21）：初回 `ruckig==0.15.3` のビルドが `scikit-build-core>=0.10` 非互換で失敗。`--build-constraints`（`scikit-build-core<0.10`）で成功。torch 2.7.1+cpu（client は GPU 不要）。pyrealsense2 / i2rt / portal / lerobot / BiYamFollower / RealSenseCamera の import OK。`lerobot-find-cameras` あり
  - **lerobot-server**（Python 3.12.3）：`uv sync --locked --extra async --extra molmoact2` が**そのまま成功**。torch 2.11.0+cu128、CUDA available、GB10 (12,1)、bf16 matmul OK。transformers 5.5.4
  - **molmoact2-official**（Python 3.12.3）：`uv sync` は `mplib` → `toppra` の順に aarch64 wheel 無しで失敗（sapien 以前に止まる）。`--no-install-package mplib --no-install-package mani-skill --no-install-package sapien --no-install-package toppra` で成功。torch 2.11.0+cu128、transformers 4.57.6、fastapi 0.141.1
  - この i2rt commit には `rpi-lgpio` の aarch64 marker は無い
- 2026-09-30: checkpoint `allenai/MolmoAct2-BimanualYAM` ダウンロード完了（25 files、約 21 GB、login 不要）
- 2026-09-30: 非駆動ツール（`~/molmoact2-setup/tools`）を client venv で pytest 13 件通過
- 2026-09-30: 公式 FastAPI server 起動 → モデルロード約 2.5 s、warmup で **`nvrtc: error: invalid value for --gpu-architecture`**（JIT reduction kernel。cu128 同梱 NVRTC が sm_121 未対応）。`/act` は 500。→ official venv の torch を cu130 に差し替えて再試行

## 結果（貼り付けたログの要約）
- 2026-09-30 00:52: torch を **2.11.0+cu130** に差し替え（official / server 両 venv、`--index-strategy unsafe-best-match`）。bf16 `prod` 再現テスト OK
- 2026-09-30 00:53: 公式 FastAPI server 再起動 → **Warmup OK (1188.7 ms)**、GPU 11.5 GB
- 2026-09-30 00:54: `dry_run_no_actuation.py --fake-sensors --queries 6` → 全 OK。actions **(30, 14)** float32、往復 mean 438 / median 433 / p95 454 / max 459 ms、server dt_ms 約 427 ms。gripper 出力 0.99、関節出力は state ±0.06 rad。`logs/g3-fake-summary-2026-09-30.json`、`logs/g3-fake-actions-2026-09-30.png`

## 問題と対応

## 次へ進む条件の充足
- [x] GPU 上でモデルロード・ダミー推論が成功（2026-09-30、公式 FastAPI 経路）
- [ ] LeRobot async server（8000）側のダミー推論は未実施（G4 の主経路として別途確認）
