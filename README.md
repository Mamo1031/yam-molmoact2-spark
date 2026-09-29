# MolmoAct2 × Bimanual YAM × DGX Spark

研究室で MolmoAct2-BimanualYAM を DGX Spark 1台（aarch64）で立ち上げるための手順書と補助ツール。

- `reference.md` — セットアップ・動作確認・非駆動評価の手順書（G0〜G5 のゲート方式）。**まず読む**
- `scripts/check_env.sh` — Spark の初期確認（§5）をワンコマンドで記録
- `scripts/dry_run_no_actuation.py` — **G4 非駆動試験**。実カメラ 3台＋実 state を公式 FastAPI server（8202）へ送り、action chunk を検査・記録する。モータ指令は一切送らない
- `scripts/dry_run_lerobot_client.py` — 同じく非駆動。LeRobot async の `robot_client` を `send_action` 無効化で走らせる（8000）
- `configs/dry_run.example.yaml` — カメラ serial・ポート・閾値の設定例
- `logs/` — ゲートごとの記録（`logs/raw/` は git 管理外）

## Spark 上での使い方（lerobot-client venv、Python 3.10）

```bash
git clone https://github.com/Mamo1031/yam-molmoact2-spark.git ~/molmoact2-setup/tools && cd ~/molmoact2-setup/tools
source ~/molmoact2-setup/workspaces/lerobot-client/.venv/bin/activate
uv pip install requests json-numpy pyyaml matplotlib pillow   # 追加依存のみ
cp configs/dry_run.example.yaml configs/dry_run.yaml          # serial を実機の値に置換

# G3: サーバだけ確認（カメラ・腕なし）
python scripts/dry_run_no_actuation.py --config configs/dry_run.yaml --fake-sensors --queries 5

# G4: 実観測（follower servers と FastAPI server を起動した状態で）
python scripts/dry_run_no_actuation.py --config configs/dry_run.yaml --queries 30 --period 1.0
```

出力（`logs/raw/dry_run_<timestamp>/`）：`summary.csv`（1 行 = 1 推論）、`chunks.jsonl`（state と action 全値）、`images/`、`actions.png`（14 次元の時系列と観測 state）、`summary.json`（latency 統計、issue 一覧）。

## 開発（この Mac）

```bash
uv sync --extra dev
uv run pytest
uv run ruff check . && uv run mypy
```

実機での動作は未検証（2026-09-29 時点）。検証できた項目は `reference.md` の該当箇所に確認日・commit・担当者を追記する。
