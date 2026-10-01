# Spark 側の構成（`~/molmoact2-setup`）を 0 から作る

DGX Spark 上の `~/molmoact2-setup` は git 管理していない。中身は「上流リポジトリのクローン」「このリポジトリの同期コピー」「補助スクリプトと OS 設定」の 3 種類で、再現に必要なものはすべてこのディレクトリとリポジトリ本体に入っている。手順の根拠と検証履歴は `reference.md`、日々の運用は `docs/setup-runbook.md`。

```
~/molmoact2-setup/
├── workspaces/
│   ├── lerobot-client/        SuveenE/lerobot  branch bimanual-yam-arms-support  commit e0bf4a54
│   │   └── i2rt/              i2rt-robotics/i2rt  commit f3dbf016  + patches/i2rt-wrist-camera-mass.patch
│   ├── lerobot-server/        SuveenE/lerobot  branch molmoact2-yam-async-inference-server  commit 8c6ae2f5（予備経路、未使用）
│   └── molmoact2-official/    allenai/molmoact2  main  commit 66b87e64（FastAPI 方策サーバ、port 8202）
├── tools/                     このリポジトリの同期コピー（rsync）。g5 ラッパーはここを実行する
├── logs/                      サーバログと run の生データ（管理しない）
├── g5, *.sh, *.py             spark/bin/ の内容
└── sudoers-can, *.rules       spark/setup/ の内容
```

## 1. 上流をクローンして venv を作る

コマンドと落とし穴は `reference.md` §6.2（lerobot-client / i2rt）、§8.1（molmoact2-official）、§9（lerobot-server）、§5（torch cu130 差し替え）にある。要点：

| workspace | Python | 必須の回避策 |
|---|---|---|
| lerobot-client | 3.10（pyrealsense2 の aarch64 wheel が cp310/cp312 のみ） | `ruckig` のビルドに `--build-constraints`（`scikit-build-core<0.10`） |
| molmoact2-official | 3.12 | `uv sync --no-install-package mplib --no-install-package mani-skill --no-install-package sapien --no-install-package toppra`、その後 torch を `2.11.0+cu130` に差し替え（cu128 は GB10 で NVRTC が落ちる） |
| lerobot-server | 3.12 | `uv sync --locked --extra async --extra molmoact2`、torch を cu130 に差し替え |

2026-09-30 時点の主要バージョン：lerobot-client = torch 2.7.1 / mujoco 3.3.5 / portal 3.7.3 / pyrealsense2 2.56.5 / ruckig 0.15.3、molmoact2-official = torch 2.11.0+cu130 / transformers 4.57.6 / fastapi 0.141.1。

## 2. i2rt にパッチを当てる

手首カメラ（D405 + ブラケット、約 0.10 kg）を重力補償モデルに加える。follower server が読む XML なので、当てないと手首が垂れて肩・肘が目標に遅れる。

```bash
cd ~/molmoact2-setup/workspaces/lerobot-client/i2rt
cp i2rt/robot_models/yam/yam_4310_linear.xml i2rt/robot_models/yam/yam_4310_linear.xml.orig
git apply ~/molmoact2-setup/tools/spark/patches/i2rt-wrist-camera-mass.patch
```

リポジトリの `assets/yam/yam_4310_linear.xml` はパッチ適用後と同じ内容（executor の FK 用）。

## 3. OS 設定（sudo）

```bash
# CANable を固定名にする（serial は lsusb -v / udevadm info で確認。rules 内の値は研究室の個体）
sudo install -m 644 spark/setup/90-can.rules /etc/udev/rules.d/90-can.rules
# RealSense の権限
sudo bash spark/setup/setup_realsense_perms.sh          # ユーザー名は murata-lab 固定。別名なら書き換える
# 再起動のたびに必要な `ip link set ... up type can bitrate 1000000` をパスワードなしで通す
sudo install -m 440 spark/setup/sudoers-can /etc/sudoers.d/can
sudo udevadm control --reload-rules && sudo udevadm trigger
```

`setup_can_left.sh` / `rename_can_right.sh` は初回に名前を割り当てたときの作業記録（同じことを 90-can.rules が恒久化している）。

## 4. スクリプトを置く

```bash
rsync -a --exclude __pycache__ dryrun scripts tests configs assets spark spark:molmoact2-setup/tools/   # Mac から
ssh spark 'cp ~/molmoact2-setup/tools/spark/bin/* ~/molmoact2-setup/ && chmod +x ~/molmoact2-setup/g5 ~/molmoact2-setup/*.sh'
```

| スクリプト | 役割 |
|---|---|
| `g5` | guarded executor のラッパー（venv 有効化 + 既定オプション）。`g5 run / pose-guide / temps / return-to-start / analyze / trace ...` |
| `start_follower_servers.sh` | CAN の bitrate を設定してから follower server（右 1234、左 1235）を起動。**両腕を支えてから** |
| `start_policy_server.sh` | 方策サーバ（8202）を `HF_HUB_OFFLINE=1` でローカルキャッシュから起動（インターネット不要） |
| `gravcomp.sh` | 片腕だけ重力補償で浮かせる（G1 の確認用） |
| `read_obs_once.py` | 片腕の観測を 1 回読む（G1 の確認用） |

チェックポイント `allenai/MolmoAct2-BimanualYAM` は初回の `start_policy_server.sh` 前に一度オンラインで取得しておく（`HF_HUB_OFFLINE` を外して起動するか `huggingface-cli download`）。

## 5. 起動の順番（毎回）

1. `~/molmoact2-setup/start_policy_server.sh`
2. 両腕を畳んで支え、`~/molmoact2-setup/start_follower_servers.sh`
3. `~/molmoact2-setup/g5 temps --seconds 5` で全モータが有効・温度が低いことを確認
4. 以降は `docs/setup-runbook.md` §3.7（pose-guide → run → return-to-start）
