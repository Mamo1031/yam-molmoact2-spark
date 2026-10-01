# MolmoAct 2 × Bimanual YAM × DGX Spark

**初期セットアップ・動作確認・Zero-shot VLA評価ガイド（研究室内共有用）**  
作成日：2026-09-28　|　改訂：2026-09-29（上流ソース照合、DGX Spark/aarch64 固有事項を追記）　|　Status: **実機未検証の初期手順**  
担当：＿＿＿＿＿＿　|　確認者：＿＿＿＿＿＿

> [!IMPORTANT]
> **目標はDGX Spark 1台で、左右YAMの制御、RealSense 3台の画像取得、MolmoAct2-BimanualYAMの非同期推論を動かすこと。**
> 下記コマンドは公開ドキュメントと参考Gistに基づき、2026-09-29 に上流ソース（脚注参照）と突き合わせたが、研究室のDGX Spark（**ARM64/aarch64**）・購入機材での通し動作は**まだ検証されていない**。各段階の結果を記録し、**VLAから実機へ動作指令を送る前に担当教員の確認を受けること**。

> [!NOTE]
> **aarch64 固有の既知リスク（2026-09-29 時点）**
> 1. 公式 `allenai/molmoact2` の `uv sync` は **`mplib` → `toppra`（→ `sapien`）に aarch64 wheel が無く Spark ではそのまま入らない**。`--no-install-package` で 4 パッケージを除外すれば入る（**2026-09-30 確認済み**、§8.1）。
> 2. `pyrealsense2` の aarch64 wheel は **Python 3.10 と 3.12 のみ**（3.11 は無い）。client 環境は 3.10 を厳守。
> 3. DGX Spark の USB は、**起動前に挿した機器が USB 2.0 に固定される**、**複数 RealSense 同時ストリームで USB コントローラが落ちる**報告がある。3台同時の成功報告は見つかっていない（§7）。
> 4. DGX OS カーネル（7.0.0-1019-nvidia）に `gs_usb` / `slcan` モジュールは**ある**（2026-09-30 確認済み、§6.1）。
> 6. **cu128 の torch wheel は GB10 で JIT カーネルが落ちる**（`nvrtc: invalid value for --gpu-architecture`、bf16 の `prod` で再現）。server / official の両環境とも **torch 2.11.0+cu130 に差し替えが必要**（2026-09-30 確認済み、§5）。
> 5. 推論速度：公式 FastAPI server（bf16、CUDA Graph なし）で **約 430 ms/回（約 2.3 Hz）** をダミー入力で実測（2026-09-30、§8.1）。1 秒 chunk の非同期実行なら成立見込みだが、実観測・CUDA Graph 有効時の再計測が必要（§9.4）。

## 目次

1. [ゴールと進め方](#1-ゴールと進め方)
2. [使用機材と事前確認](#2-使用機材と事前確認)
3. [機器の組み立て・配線](#3-機器の組み立て配線)
4. [安全上の注意](#4-安全上の注意)
5. [DGX Sparkの初期確認](#5-dgx-sparkの初期確認)
6. [YAM/CANの単体動作確認](#6-yamcanの単体動作確認)
7. [RealSense 3台の確認](#7-realsense-3台の確認)
8. [MolmoAct 2推論環境の構築](#8-molmoact-2推論環境の構築)
9. [LeRobot async inference（本命構成）](#9-lerobot-async-inference本命構成)
10. [安全確認・実機評価への移行](#10-安全確認実機評価への移行)
11. [トラブルシューティング](#11-トラブルシューティング)
12. [提出物と完了条件](#12-提出物と完了条件)
13. [参考リンク](#13-参考リンク)
14. [検証履歴](#14-検証履歴)

---

## 1. ゴールと進め方

今回の**第一目標**は、左右YAMと3台のカメラから得た実観測をDGX Sparkに入力し、**MolmoAct2-BimanualYAMが行動チャンクを生成できることを、ロボットを動かさず確認する**ところまで。安全審査後、低速の実機実行に進む。

```text
D435 (top) ─────────┐
D405 (left) ────────┼── [DGX Spark: robot client / Python 3.10]
D405 (right) ───────┘          │           ↑  YAM state
                               │ localhost / gRPC (8000)
                               ▼
                   [policy server / Python 3.12]
                     MolmoAct2-BimanualYAM
                               │ action chunk
                               ▼
                [robot client / YAM follower servers (1234 / 1235)]
                      │                    │
                  CANable L            CANable R
              (can_follower_l)      (can_follower_r)
                      │                    │
                    YAM L                YAM R
```

**同じ物理PC上の別プロセス・別Python環境**として動かす。初期段階ではGELLO YAM ActiveとFlexPointは使用せず、**Research Kitの標準グリッパ／公式構成**を使う。GELLOは後のデータ収集段階、FlexPointは標準構成での動作確認後に扱う。

| Gate | 作業 | 次へ進む条件 |
|---|---|---|
| G0 | 組立・配線・安全確認 | ロボットが安定固定され、停止手順を全員が把握 |
| G1 | YAM単体確認 | 左右それぞれのCAN通信・state取得が成功 |
| G2 | カメラ単体／同時取得 | 3台のRGB画像とserial/roleの対応を保存 |
| G3 | MolmoAct 2環境 | GPU上でモデルロード・ダミー推論が成功 |
| G4 | 実観測による**非駆動**試験 | action shape/値/順序/レイテンシが確認できる |
| G5 | 実機動作 | **教員承認後のみ**、低速・短時間・制限付きで実施 |

**フォールバック条件**：G3 で aarch64 の依存問題が解決しない、または G4 で action queue が枯渇する場合は、カメラ＋CAN を別の x86 PC（または Jetson）に置き、Spark を policy server 専用（10 GbE 経由）にする構成へ切り替える。この切替は本書の §9 をほぼそのまま「Server PC / Client PC」構成（参考Gistの原型）に戻すことに相当する。

---

## 2. 使用機材と事前確認

| 項目 | 数量 | 状態・備考 |
|---|---:|---|
| MolmoAct 2 Research Kit（YAM Standard左右、フレーム等） | 1式 | 購入済み。キットページの明記内容：YAM Standard ×2、2060 アルミプロファイル 80 cm ×1、伸縮式カメラデスクマウント ×1、特大Gクランプ ×2 [^kit] |
| YAM用標準グリッパ | 左右各1 | **2026-09-30 現物確認：リニアレール型（`linear_4310` 想定）**。SDK 既定値と一致 [^i2rt-readme] |
| CANable + 分岐ハーネス | 左右各1 | **2026-09-30 現物確認：同梱あり**。USB-C の CANable に、黄 XT30（電源）と黒 4 ピン（アーム側）を持つ I2RT 製分岐ハーネスが付属。詳細は `docs/hardware-wiring.md` §2.3 |
| YAM用 24 V 電源 | 左右各1 | **2026-09-30 現物確認：同梱あり**。24.0 V / 14.0 A、XT30 出力、IEC AC コード |
| DGX Spark | 1 | 購入済み。ロボット横に設置可能。データ用 USB-C ×3、電源用 USB-C ×1（左端）[^spark-hw] |
| RealSense D435 | 1 | **研究室保有済み**。topカメラ（キットページの "overhead" 用途に対応 [^kit]） |
| RealSense D405 | 2 | **購入済み**。left/rightカメラ（キットページの "wrist" 用途に対応 [^kit]） |
| Anker USB-C データハブ（11-in-1、A83085A1/A8308） | 1 | **購入済み**。付属65W ACアダプタを使用 |
| USB-A ↔ **USB 3.0 Micro-B**ケーブル | 2 | **購入済み**。D405用 |
| USB-C ↔ USB-C **データ対応**ケーブル | 必要数 | 研究室保有品を使用。映像/通信に使えるものを選ぶ |
| CANable用USBデータケーブル | 左右各1 | 同梱品で足りるか確認 |
| DGX Spark純正電源 | 1 | 240 W。Anker ハブの PD ポートからは給電しない |
| ディスプレイ/入力機器、ネットワーク | 1式 | Spark初期設定用 |

**未購入扱いにしないもの：D435、D405×2、Ankerハブ、D405用ケーブル×2。**

### 開封・設置前のチェック

- [x] 左右YAM、標準グリッパ、**CANable、CANケーブル、24 V 電源**の同梱有無を確認し、写真を残す（2026-09-30、`logs/photos-2026-09-30/`）
- [x] グリッパ型式を確認（2026-09-30：リニア型、`linear_4310` 想定）
- [ ] YAMとフレームの固定具・カメラマウント・クランプを確認
- [ ] D435およびD405の型番・外観・ケーブル端子を確認
- [ ] FlexPointは現段階では取り付けない
- [ ] 両腕と周辺物体が干渉しない範囲を確保
- [ ] 初回通電時に2名以上で確認（操作担当／安全監視担当）

---

## 3. 機器の組み立て・配線

### 3.1 機械配置

Research Kitの[I2RT説明ページ](https://i2rt.com/products/molmoact-2-research-kit)、[YAMドキュメント](https://doc.i2rt.com/products/yam)、[MolmoAct 2公式のBimanual YAM Setup](https://github.com/allenai/molmoact2#bimanual-yam-setup)（README は参照画像と Google Sheets の部品表を提示 [^molmoact-readme]）を見ながら組み立てる。

- YAM Left / Rightの物理的位置、作業台高さ、フレーム固定、カメラの向きを写真で記録する。
- D435 = top（俯瞰）、D405 = left/right（近接）。この対応の根拠はキットページの "overhead D435 / wrist D405×2" [^kit]。参考Gist と LeRobot fork の README はカメラ機種を明記していない [^gist]。**モデルが期待する画像順序 `[top, left, right]` を入れ替えない** [^hf-card]。
- 公開データのカメラ配置をできるだけ再現する。正確な外部パラメータが不明な場合に、独自の変換行列を仮定して制御へ流用しない。
- 配線がロボットの関節・可動範囲に触れないよう固定する。

### 3.2 USB配線（推奨案：改訂）

DGX Spark のデータ用 USB-C は 3 ポート（実測 USB 3.2 Gen 2x2）[^spark-usb]。複数 RealSense を 1 つのハブ配下に集めると帯域とコントローラ負荷が集中するため、**カメラ 3 台は Spark のデータポートに 1 台ずつ直結**し、帯域をほとんど使わない CANable と入力機器をハブ側に寄せる。

```text
DGX Spark（純正電源を左端の電源専用 USB-C に接続）
│
├─ USB-C データ #1 ── USB-C/C（USB 3.x）──────────── D435 top
├─ USB-C データ #2 ── USB-C→A 変換 or C/C ── Micro-B ─ D405 left
└─ USB-C データ #3 ── Anker A8308（付属ACアダプタを接続）
                        ├─ USB-A 5Gbps #1 ─ USB-A/USB3 Micro-B ─ D405 right
                        ├─ USB-A 5Gbps #2 ─ CANable left（USB端子を現物確認）
                        ├─ USB-C 5Gbps     ─ CANable right（USB端子を現物確認）
                        └─ （キーボード・マウス等）
```

D405 の一方がハブ経由になるのは、Spark のデータポートが 3 つしか無く、ハブにも 1 ポート必要なため。**カメラ 2 台を Spark 直結、1 台をハブ経由**とし、不安定ならハブ側の D405 を Spark 直結に入れ替えて切り分ける（その場合は CANable とキーボードを USB-C→A 変換で 1 ポートに集約）。

> [!CAUTION]
> - **電源投入前に USB 機器を挿しておくと USB 2.0（480 Mbps）で固定される報告がある** [^spark-usb2]。**カメラは Spark 起動後に挿す**。`lsusb -t` で **5000M 以上**を確認する。
> - **Anker A8308 の 10 Gbps USB-A ポートはメーカー仕様上「充電非対応」**。CANable へのバス給電を当てにした配線では避け、**給電可能な 5 Gbps USB-A/USB-C データポート**を使う。Anker の **USB PD 専用ポートはデータ非対応**であり、DGX Spark 自体への給電にも用いない。
> - 上流帯域はハブ内で共有する。カメラ接続が不安定なら、D435 単体 → D405 を 1 台ずつ追加し、毎回 `lsusb -t` を確認する。

### 3.3 CAN・電源配線

**配線の詳細（コネクタ、電源、終端、写真）は [`docs/hardware-wiring.md`](docs/hardware-wiring.md) を参照**（2026-09-30 作成、実機未検証）。

```text
YAM Left  ── CAN ── CANable L (can_follower_l) ── USB ── Anker hub / DGX Spark
YAM Right ── CAN ── CANable R (can_follower_r) ── USB ── Anker hub / DGX Spark
左右YAM ── 対応する24 V電源（付属品の仕様を確認）
```

左右のCANableに**LEFT / RIGHTのラベル**を貼る。`can0` / `can1`は再接続で入れ替わるため、udev で固定名を付ける。**固定名は `can_follower_l` / `can_follower_r` にする**。LeRobot fork の follower server 起動スクリプトがこの名前を探すため [^setup-servers]。手順は §6.1。

---

## 4. 安全上の注意

> [!WARNING]
> **生成行動をいきなり実機に送信しない。** 初回は単体確認 → 画像/state収集 → 推論結果ログ（モータ指令なし） → 承認 → 低速実機評価、の順に進める。

- 物理的に電源を遮断する手順を確認し、操作担当以外にも停止できる人を置く。`Ctrl+C`だけを緊急停止手段とみなさない。
- ロボットの稼働領域に身体や壊れやすいものを置かない。最初は把持物なし、周囲に干渉物なし。
- YAMは**CAN制御のDM（Damiao）系モータ**（DM4340 肩、DM4310 肘/手首）[^yamdoc]。**Dynamixelは将来作成するGELLO YAM Active側の話であり、YAM follower本体の駆動方式ではない。**
- **重力補償モード（`zero_gravity_mode=True`）はトルク指令ループを回す**。位置指令はしないが read-only ではなく、起動すると腕が脱力して手で動かせる状態になる。**起動時は腕を手で支える**。SDK の重力補償ループは 100 Hz を下回ると警告する [^i2rt-readme]。
- **リニアグリッパ（`linear_4310` / `linear_3507`）は、起動前に完全に閉じておくか、キャリブレーションを実行する** [^i2rt-readme]。
- **モータの工場既定は 400 ms timeout**。400 ms 指令が途絶えると damping モードに入る [^i2rt-readme]。初期段階（G1〜G4）では**無効化しない**。ただし既定のままだと**長時間の teleop / 評価中に制御ループが一瞬詰まっただけで腕が突然崩れる**ことが参照実装で報告されている [^yam-ref]。G5 に進む際、無効化する場合の手順は §10 に記す。
- `np.zeros(6)` / `np.zeros(7)`など現在姿勢から大きく変わる目標位置を初回テストで無条件に送らない（i2rt README の例は 6 要素、doc.i2rt.com の例は 7 要素で、ドキュメント間で不一致がある [^i2rt-readme] [^yamdoc]）。
- 実機動作前に関節位置制限、ステップ当たりの最大変位、速度制限、左右衝突、机との衝突、古いaction/stale observationに対する停止条件を確認する。**action queueが枯渇した場合の安全動作**も定める。
- モータのレジスタ書き込み（ID、timeout など）を行うときは、バスを使う全プログラムを止める。書き込みに成功したレジスタだけを save する（未書き込みのレジスタを save すると他の未保存レジスタが戻る現象が報告されている）[^i2rt-readme]。

**停止条件（例）**：予期しない急動作、発振、CAN errorの継続、RealSense切断、actionのNaN/Inf、関節限界超過、左右armの干渉、指令更新の途絶。異常時は指令を止め、必要なら安全に電源遮断し、原因特定まで再開しない。

---

## 5. DGX Sparkの初期確認

DGX Sparkは**ARM64/aarch64**、GPU は GB10（compute capability 12.1、CUDA 13 世代）。古いx86_64向けのwheelをそのまま導入しない。

**PyTorch の前提（2026-09-29 時点）** [^torch-spark]
- `torch >= 2.11` は PyPI の aarch64 wheel が CUDA 13 対応で、`pip install torch` だけで GPU が使える。
- **`torch <= 2.10` の aarch64 wheel は CPU-only**。依存解決で古い torch に落ちると GPU が見えなくなる。
- PyTorch の cu128 index にも 2.10 / 2.11 の aarch64 wheel がある（本書の LeRobot fork は cu128 index を使う）。
- sm_121 は sm_120 とバイナリ互換。「capability 12.1 > 12.0」の警告は無視してよい。
- CUDA 12 前提の拡張パッケージ（古い flash-attn、vLLM 等）は `libcudart.so.12` エラーになる。本書の構成では不要。
- **【2026-09-30 実機確認】cu128 index の torch 2.11.0 は import と matmul は通るが、JIT コンパイルされる reduction カーネル（例：bf16 の `x.prod(dim=1)`）が `nvrtc: error: invalid value for --gpu-architecture (-arch)` で失敗する**。MolmoAct2 の `predict_action` がこれを踏んで 500 になった。cu130 の wheel（NVRTC 13.0.88）に差し替えると解消する。pyproject が cu128 index を固定している repo では次のように上書きする：

```bash
uv pip install --python .venv \
  --index-url https://download.pytorch.org/whl/cu130 --index-strategy unsafe-best-match \
  "torch==2.11.0+cu130" "torchvision==0.26.0+cu130"
# 以後は uv run --no-sync で起動する（uv run だけだと lock に戻される）
```

```bash
uname -m                         # 期待: aarch64
cat /etc/os-release
nvidia-smi
lsusb
lsusb -t
df -h
ip addr
modinfo gs_usb slcan can_raw     # SocketCAN モジュールの有無（§6.1）
```

まず一般的な診断ツールがなければ導入する（研究室のOS管理方針に従う）。

```bash
sudo apt update
sudo apt install -y git curl usbutils v4l-utils can-utils iproute2 \
  build-essential python3-dev linux-headers-$(uname -r)
```

`build-essential` 以降は i2rt SDK のインストール要件 [^i2rt-readme]。

`uv`が未導入なら[公式](https://docs.astral.sh/uv/getting-started/installation/)の手順で導入：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
exec "$SHELL"
uv --version
```

ログ記録（リポジトリの `scripts/check_env.sh` を使うと下記と `modinfo` をまとめて保存できる）：

```bash
mkdir -p ~/molmoact2-setup/logs
{
  date -Is
  uname -a
  cat /etc/os-release
  nvidia-smi
  lsusb -t
  modinfo gs_usb 2>&1 | head -5
} | tee ~/molmoact2-setup/logs/system-info.txt
```

- [x] `uname -m`とGPU名を記録（2026-09-30：aarch64、NVIDIA GB10、Ubuntu 24.04.5、kernel 7.0.0-1019-nvidia）
- [x] `nvidia-smi` の driver / CUDA version を記録（2026-09-30：580.178.04 / CUDA 13.0）
- [ ] 空き容量を確認（checkpoint 約 29 GB [^hf-card] ＋ 3 つの Python 環境。**100 GB 以上**の余裕を推奨）
- [ ] SparkとAnkerハブの両方に、それぞれ正しい電源を接続
- [ ] インターネット/Hugging Face接続を確認（checkpoint は gated ではなく login 不要 [^hf-card]）
- [x] `modinfo gs_usb` の結果を記録（2026-09-30：`/lib/modules/7.0.0-1019-nvidia/kernel/drivers/net/can/usb/gs_usb.ko.zst` あり）

---

## 6. YAM/CANの単体動作確認

**最初は左腕だけ通電・接続**する。以降のコマンド例でパスやPython環境が異なる場合は、使用する[I2RT SDKの該当commit](https://github.com/i2rt-robotics/i2rt)に合わせて調整・記録する。

### 6.1 CANデバイス認識

YAM 付属の CANable は **candlelight firmware（Linux では `gs_usb` ドライバ）で出荷される** [^i2rt-udev]。

```bash
modinfo gs_usb                    # モジュールが無ければ下記を試す
# sudo apt install -y linux-modules-extra-$(uname -r)   # DGX OS の kernel に無い場合
ip link
ls -l /sys/class/net/can*         # CANが存在しない場合はエラーでもよい（原因を調査）
```

`gs_usb` がどうしても無い場合は DKMS でビルドする必要がある。その時点で報告し、勝手にカーネルを入れ替えない。

CAN 名が `can0` と確認できた場合のみ：

```bash
sudo ip link set can0 up type can bitrate 1000000
ip -details -statistics link show can0
candump can0     # 何か流れるか確認（Ctrl+C で終了）
```

YAMのCAN通信は**1 Mbps** [^yamdoc]。**bitrate 設定は再起動のたびに必要**。`reset_all_can.sh`（§6.2 の client 環境では `i2rt/scripts/reset_all_can.sh`）は接続されている**全 CAN インターフェース**を down → up（1 Mbps）するので、腕を再接続したあとにも実行する [^i2rt-readme]。

**固定名の付与**（片腕ずつ接続して serial を取る）[^i2rt-udev]：

```bash
udevadm info -a -p /sys/class/net/can0 | grep -i serial
```

`/etc/udev/rules.d/90-can.rules`：

```text
SUBSYSTEM=="net", ACTION=="add", ATTRS{serial}=="<LEFT_CANABLE_SERIAL>",  NAME="can_follower_l"
SUBSYSTEM=="net", ACTION=="add", ATTRS{serial}=="<RIGHT_CANABLE_SERIAL>", NAME="can_follower_r"
```

名前は `can` で始まり 13 文字以内。`sudo udevadm control --reload && sudo udevadm trigger` のあと抜き差しして `ip link` で確認。

**注**：公式 `allenai/molmoact2` の評価設定（`examples/yam/configs/yam_left.yaml`）は `channel: can_left` / `can_right` を使う [^yam-yaml]。本書は LeRobot fork の follower server に合わせて `can_follower_l/r` を採用する。公式の評価スクリプトを使う場合は、その YAML 側の `channel` を `can_follower_l/r` に書き換える（udev の名前を増やさない）。

### 6.2 SDK環境を用意する

まず[参考Gist](https://gist.github.com/SuveenE/6bc2b822ac44807565729c2b0ebb1cb2)の**clientブランチ**を用意する [^gist]。ここでは2環境を明示的に分ける。

```bash
mkdir -p ~/molmoact2-setup/workspaces
cd ~/molmoact2-setup/workspaces

git clone --branch bimanual-yam-arms-support \
  https://github.com/SuveenE/lerobot.git lerobot-client
cd lerobot-client
git submodule update --init --recursive      # i2rt を submodule として取得

git rev-parse HEAD
git -C i2rt rev-parse HEAD

uv venv --python 3.10       # 3.10 厳守（pyrealsense2 の aarch64 wheel は cp310 / cp312 のみ）
source .venv/bin/activate
# ruckig（i2rt の依存）は scikit-build-core>=0.10 でビルドに失敗するため build constraint を付ける
printf 'scikit-build-core<0.10\n' > /tmp/build-constraints.txt
uv pip install --build-constraints /tmp/build-constraints.txt -e ./i2rt
uv pip install --build-constraints /tmp/build-constraints.txt -e ".[yam,async,intelrealsense]"
python -c "import pyrealsense2 as rs, i2rt, lerobot; print('imports ok')"
```

**【2026-09-30 確認済み】** Python 3.10.21、i2rt commit `f3dbf01`、lerobot-client `e0bf4a54`。build constraint なしでは `ruckig==0.15.3` が `ERROR: Use build.targets instead of cmake.targets for scikit-build-core >= 0.10` で失敗、constraint ありで成功。pyrealsense2（`__version__` 属性なし）、i2rt、portal、lerobot、`BiYamFollower`、`RealSenseCamera` の import と `lerobot-find-cameras` を確認。torch は 2.7.1+cpu（client は GPU 不要）。

> [!NOTE]
> - client branch は `requires-python >= 3.10`。extras は `yam`（portal）、`async`（grpcio, matplotlib）、`intelrealsense`（`pyrealsense2 >=2.55.1,<2.57`）[^client-pyproject]。
> - i2rt の新しい版は linux aarch64 で `rpi-lgpio` を依存に加える platform marker を持つとの情報があった [^i2rt-readme] が、**submodule の commit `f3dbf01` には無い**（2026-09-30 確認）。submodule を更新した場合は再確認する。
> - ARM64でインストールが失敗した場合、無理にアーキテクチャ違いのパッケージを入れず、**エラーログ・OS/Python/torch/pyrealsense2の版**を記録して報告する。

### 6.3 左腕・右腕の順に動作確認

I2RT SDKが導入できたら、**周囲の安全確認後**に重力補償の公式例を単独で試す。実行前に`--channel`が意図した腕であることを必ず確認する。**起動時は腕が脱力するので手で支える。**

```bash
# lerobot-client の仮想環境内
cd ~/molmoact2-setup/workspaces/lerobot-client/i2rt
# 1) まず疎通だけ：モータ ID 1〜7 を順に enable→disable して応答を見る（トルク指令なし、腕は畳んだまま）
python i2rt/motor_config_tool/ping_motors.py --channel can_follower_l
# 2) 重力補償（腕を手で支えてから）
python i2rt/robots/motor_chain_robot.py --channel can_follower_l --gripper_type linear_4310 --operation_mode gravity_comp
```

**【2026-09-30 確認：submodule commit `f3dbf01` の CLI】** 引数は `--channel` / `--gripper_type` / `--operation_mode` の 3 つ（`--arm` は無い。公式 README の新しい版とは異なる）。**`--gripper_type` の既定値は `crank_4310` なので、必ず `linear_4310` を明示する**。`--operation_mode` は `gravity_comp`（既定。重力補償で待機）/ `stay_current_qpos`（現在姿勢を PD 保持）/ `test_gripper`（**全関節 0 rad を指令する**ので初回は使わない）。同梱グリッパの型式が異なる場合は `crank_4310` / `linear_3507` / `flexible_4310` / `no_gripper` から選ぶ。**リニアグリッパは起動前に全閉しておく。**

次に、I2RTのAPIで観測を確認する。**このコードは目標関節角を指令しないが、重力補償のトルク指令は流れる**（真の read-only ではない）。

```python
from i2rt.robots.get_robot import get_yam_robot
from i2rt.robots.utils import GripperType

# zero_gravity_mode=True is the default: the arm becomes compliant. Support it by hand.
robot = get_yam_robot(
    "can_follower_r", gripper_type=GripperType.LINEAR_4310
)  # zero_gravity_mode=True is the default
try:
    obs = robot.get_observations()
    for k, v in obs.items():
        print(k, len(v), v)
    # expected (commit f3dbf01): joint_pos 6, gripper_pos 1, joint_vel 7, joint_eff 7
finally:
    robot.close()
```

観測キーは `joint_pos`(6)、`gripper_pos`(1)、`joint_vel`(7)、`joint_eff`(7) [^i2rt-readme]。**【2026-09-30 確認：commit `f3dbf01` では vel/eff が 7 要素（グリッパ込み）で、`gripper_vel` / `gripper_eff` キーは存在しない**。`gripper_pos` は 0（閉）〜1（開）に正規化されている。右腕についても単独で同じ確認を実施する。両腕接続後にleft/right mappingを固定する。

- [x] Right の CANable 認識・`can_follower_r` 固定・1 Mbps up（2026-09-30。CANable 2.5 ElmueSoft candleLight、serial 208E37AE45465006。研究室の都合で右腕から着手）
- [x] Left：`ping_motors.py` で motor 1〜7 応答（2026-09-30、CANable serial 20A7378545465006 = `can_follower_l`）
- [x] Leftで正常にstate取得（2026-09-30：joint_pos 6 / gripper_pos 1）
- [x] Right：`ping_motors.py` で motor 1〜7 応答（2026-09-30）
- [x] Rightで正常にstate取得（2026-09-30：joint_pos 6 / gripper_pos 1 / joint_vel 7 / joint_eff 7）
- [x] 標準グリッパを識別・キャリブレーション方法を確認（2026-09-30：linear_4310、SDK が起動時に自動校正）
- [x] 両腕同時接続でCAN errorなし（2026-09-30：follower servers 1234/1235 経由で 14D state 取得、342 Hz）
- [x] 6関節＋gripper×2 = **14次元**の意味と並び順を記録（2026-09-30 実測で確認）。並びは **left(6関節+gripper) → right(6関節+gripper)** で、LeRobot の `bi_yam_follower`（`left_joint_0.pos … left_gripper.pos, right_…`）[^setup-servers] と公式 `gello_min` の `BimanualRobot`（left を先に concatenate）[^yam-yaml] で一致

---

## 7. RealSense 3台の確認

### 7.1 まず1台ずつ認識を確認

**カメラは Spark 起動後に挿す**（§3.2）。クライアント環境（`lerobot-client/.venv`）で、LeRobotのカメラ検索を試す：

```bash
cd ~/molmoact2-setup/workspaces/lerobot-client
source .venv/bin/activate
python -c "import pyrealsense2 as rs; print('pyrealsense2: OK')"
lerobot-find-cameras realsense
```

**【2026-09-30 確認】** 初回は `lerobot-find-cameras realsense` が 0 台を返した。原因は権限で、Intel 公式の `99-realsense-libusb.rules` を `/etc/udev/rules.d/` に入れ、ユーザーを `video` グループに追加し、カメラを挿し直すと検出された（DGX OS には RealSense の udev ルールが無い）。RealSense SDKのCLIがインストールされていれば、`rs-enumerate-devices`でも確認できる。あわせて：

```bash
lsusb
lsusb -t
```

**各機器のserial number**を取得し、接続roleを固定する。

| Role | 型番 | Serial number | 接続先 |
|---|---|---|---|
| `top` | D435 | `138422071505`（2026-09-30 確認、FW 5.15.0.2） | Spark直接 |
| `left` | D405 | `260322279321`（2026-09-30 確認） | Anker 5 Gbps USB-A |
| `right` | D405 | `260522272252`（2026-09-30 確認、FW 5.15.1.55） | Anker 5 Gbps USB-A |

> [!NOTE]
> DGX Spark 上の報告 [^spark-rs]：V4L2 backend ではカメラが 0 台に見え、**RSUSB backend** が必要だった。また 848×480@60 の複数ストリームで USB コントローラ（xHCI）が停止し、librealsense 2.58.4 への更新後に D435 1 台の 720p30（depth+color+IR×2）で 80 分間ドロップなしを確認したという。**3 台同時の報告は無い**。pip の `pyrealsense2` wheel がどの backend でビルドされているかは実機で確認する（`rs-enumerate-devices` が 0 台なら backend を疑う）。

### 7.2 同時RGB取得

- 最初はRGBのみ。D435を単独確認 → D405 leftを追加 → D405 rightを追加。
- 参考Gistは**3台それぞれ `640 × 360, 30 fps`**を指定している [^gist]。**【2026-09-30 確認】** D435 も D405 も 640×360@30 の rgb8 プロファイルを持ち、LeRobot `RealSenseCamera` で 30.9 fps を確認。ただし**既定の warmup では初回 `read()` が失敗する**ので、camera 設定に `"warmup_s": 2` を加える。
- 少なくとも数分間の同時ストリーム取得、左右の正しい映像、USB切断の有無、実測fpsを確認する。`dmesg -w` を別ターミナルで流し、xHCI のエラーが出ないか監視する。
- `lsusb -t`でUSB 2.0（480M）に落ちていないか確認する。ハブとケーブルの品質、同時帯域を疑う。
- キャプチャした**3枚の画像**に`top`、`left`、`right`とラベルを付けて報告する。

- [x] serialと物理roleを固定（2026-09-30：top 138422071505 / left 260322279321 / right 260522272252）
- [x] top/left/right画像を取得・保存（2026-09-30）
- [x] 数分間の同時取得に成功（2026-09-30：3 台同時 60 s、各 30.0 fps、ドロップ 0、カーネルエラーなし）
- [x] RGB画像の上下/左右・解像度・露出が適切（2026-09-30：top / right）

---

## 8. MolmoAct 2推論環境の構築

最終目標は**LeRobot async inference**（次節）だが、切り分けのため、まずモデルロードとダミー推論をハードウェア非駆動で確認する。公式の代替手段は[MolmoAct 2 FastAPI YAM server](https://github.com/allenai/molmoact2#5-inference-servers)。こちらは**8202番ポート**であり、次節のLeRobot async server（**8000番**）とは別方式。混同しないこと。

**方針（改訂）**：G3/G4 は次の二段構えとし、**Spark で先にインストールできた方**で進める。

| | LeRobot async server（§9.1、8000） | 公式 FastAPI server（§8.1、8202） |
|---|---|---|
| 位置付け | **主**。本命構成そのもの | **従**。切り分け・非駆動試験用 |
| aarch64 見込み | `molmoact2` extra は transformers/peft/scipy のみで sapien を含まない。`uv.lock` に torch 2.11.0+cu128 の aarch64 wheel がある [^server-pyproject]。**入る見込みが高い** | pyproject が `mani-skill → sapien` を要求し、**sapien に aarch64 wheel が無い**。回避策が要る |
| 非駆動試験のしやすさ | robot_client の action 送信を差し替える必要あり | `/act` の schema が明確で HTTP client を書くだけ |

### 8.1 公式モデル環境（診断用・任意）

```bash
cd ~/molmoact2-setup/workspaces
git clone https://github.com/allenai/molmoact2.git molmoact2-official
cd molmoact2-official
git rev-parse HEAD
uv sync
```

公式 pyproject は `requires-python >=3.11,<3.13`、`torch==2.11.0` / `torchvision==0.26.0` を **cu128 index 固定**、`transformers >=4.57,<4.58`、`mani-skill >=3.0.1` [^molmoact-pyproject]。

**【2026-09-30 確認済み】** 素の `uv sync` は `mplib==0.1.1`、次に `toppra==0.6.10` が aarch64 wheel 無しで失敗（sapien まで到達しない）。次で成功した（commit `66b87e6`、Python 3.12.3）：

```bash
uv sync --no-install-package mplib --no-install-package mani-skill \
        --no-install-package sapien --no-install-package toppra
# 続けて §5 の手順で torch を cu130 に差し替える（cu128 のままだと /act が 500 になる）
```

除外した 4 つはシミュレーション評価（ManiSkill）用で、FastAPI server には不要。pyproject/lock は書き換えていない。

```bash
uv run --no-sync python -c 'import torch; print(torch.__version__, torch.version.cuda, torch.cuda.is_available()); print(torch.cuda.get_device_name(0)); print(torch.cuda.get_device_capability(0))'
uvx --from huggingface_hub hf download allenai/MolmoAct2-BimanualYAM   # 25 files、約 21 GB（実測）、login 不要
```

ARM64で`uv sync`が失敗し、上記の回避でも入らない場合は、そのまま先へ進まず報告する。**aarch64対応PyTorchが存在しても、このrepositoryの全依存ライブラリがSparkで動く保証にはならない。**

モデルserver起動例（**この段階ではロボットは接続しない**）[^host-server]：

```bash
# repository root で実行する（--no-sync を付けないと lock の cu128 torch に戻される）
uv run --no-sync python examples/yam/host_server_yam.py \
  --host 127.0.0.1 --port 8202 --dtype bfloat16
```

**【2026-09-30 確認済み】** モデルロード約 2.5 s（5 shards）、warmup 1189 ms、GPU メモリ 11.5 GB。ダミー RGB×3（640×360）+ 14D state で `/act` → `actions` (30, 14) float32、往復 **約 430 ms/回**（中央値 433 ms、p95 454 ms、6 回）。NaN/Inf なし、gripper 出力は約 0.99。ログ：`logs/g3-fake-summary-2026-09-30.json`、`logs/g3-fake-actions-2026-09-30.png`。

公式 README の例は `--host 0.0.0.0`（デフォルト）。本書は 1 台構成なので**意図的に 127.0.0.1 に限定**している。他のフラグ：`--repo-id`（既定 `allenai/MolmoAct2-BimanualYAM`）、`--device`（既定 `cuda:0`）、`--dtype`（bfloat16 / float16 / float32）、`--no-warmup`、`--cuda-graph`。

**`/act` の request / response（`json_numpy` 形式）** [^host-server]：

| フィールド | 型 | 備考 |
|---|---|---|
| `top_cam`, `left_cam`, `right_cam` | ndarray (H, W, 3) uint8 RGB | 3 枚とも必須 |
| `instruction` | str | タスク文 |
| `state` | ndarray (14,) float32 | 7 × 2 腕 |
| `num_steps` | int（任意、既定 10） | flow-matching のステップ数 |
| `timestamp` | float（任意） | |
| → `actions` | ndarray (N, D) float32 | N（chunk 長）・D は実測で記録 |
| → `dt_ms` | float | サーバ側推論時間 |

公式の client 実装 `examples/yam/molmoact_client.py` は **action を送信しない純推論クラス**で、観測 dict のキーは `left_camera_rgb / front_camera_rgb（= top）/ right_camera_rgb / joint_positions(14)` [^molmoact-client]。G4 の非駆動試験（リポジトリ `scripts/dry_run_no_actuation.py`）はこれを土台にする。

`/act`は推論用API。**単なる`curl` GETをモデル推論・health check成功と見なさない**こと。まずダミーRGB×3 + 14D state で 1 回推論し、推論時間を記録する。VRAM 目安は bf16 で 10〜16 GB、fp32 で約 26 GB [^molmoact-readme]。

- [x] GPUがPyTorchから見える（2026-09-30：`torch.cuda.is_available()` True、capability (12, 1)、torch 2.11.0+cu130）
- [x] checkpoint download完了（2026-09-30：snapshot `8dcbed66`）
- [x] モデルロード成功（2026-09-30）
- [x] ダミーRGB ×3 + 14D stateでaction chunkを生成（2026-09-30：(30, 14)）
- [x] model入出力のshape・latency（2026-09-30：server `dt_ms` 約 427 ms、往復約 433 ms）

---

## 9. LeRobot async inference（本命構成）

参考：[SuveenE: Running MolmoAct2 on bimanual YAM arms (async inference)](https://gist.github.com/SuveenE/6bc2b822ac44807565729c2b0ebb1cb2)（最終更新 2026-06-02）[^gist]。Gistの「Server PC」と「Client PC」を**Spark上の独立した2ディレクトリ/2環境**に置き換える。Gist は server 側に **cu128 wheel と NVIDIA driver 570.86 以上**を要求している。Spark の driver はこれより新しい。

### 9.1 Server側の構築（Python 3.12）

```bash
cd ~/molmoact2-setup/workspaces
git clone --branch molmoact2-yam-async-inference-server \
  https://github.com/SuveenE/lerobot.git lerobot-server
cd lerobot-server
git rev-parse HEAD
uv sync --python 3.12 --locked --extra async --extra molmoact2
uv run python -c 'import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))'
```

server branch は `requires-python >= 3.12`、`torch >=2.7,<2.12` を cu128 index から取得、`molmoact2` extra = transformers + peft + scipy、`async` extra = grpcio + matplotlib [^server-pyproject]。`torch.__version__` が **2.11 以上**で `cuda.is_available()` が True であることを必ず確認する（2.10 以前に落ちていたら CPU-only）。

**【2026-09-30 確認済み】** `uv sync --locked` は**そのまま成功**（commit `8c6ae2f5`、Python 3.12.3、torch 2.11.0+cu128、transformers 5.5.4、grpcio 1.73.1）。ただし cu128 では bf16 `prod` の JIT が NVRTC エラーになるため、§5 の手順で **torch 2.11.0+cu130 に差し替え済み**。以後の起動は `uv run --no-sync`。

失敗した場合：`uv.lock`、`pyproject.toml`、Python/torch/CUDA/architectureを確認し、**エラーをそのままIssueに残す**。担当教員に相談せずlockを大幅変更したり、`--no-deps`等で依存解決を飛ばしたりしない。

server起動（**Terminal S**）：

```bash
cd ~/molmoact2-setup/workspaces/lerobot-server
uv run --no-sync python -m lerobot.async_inference.policy_server \
  --host=127.0.0.1 \
  --port=8000 \
  --fps=30
```

Gist は `--host=0.0.0.0`（client が別 PC のため）。本書は 1 台構成なので 127.0.0.1 に限定する。serverは**起動したまま**にして、次のclientから接続する。`--fps=30`は**30回/秒のモデル推論速度の保証ではない**。

### 9.2 Client側・YAM follower server（Python 3.10）

6節で作成した`lerobot-client`を使用。**実機の可動域・監視者・停止方法を再確認してから**以下を実施する。Gist どおり client 側は **2 ターミナル**で動かす。

```bash
# Terminal R1
cd ~/molmoact2-setup/workspaces/lerobot-client
source .venv/bin/activate

# 全 CAN interface を down/up (1 Mbps) する。左右の固定名が見えていることを先に確認。
ip link | grep can_follower
bash i2rt/scripts/reset_all_can.sh

# follower server のみ起動（leader / teaching handle は起動しない）
python -m lerobot.scripts.setup_bi_yam_servers --eval
```

`setup_bi_yam_servers --eval` は **`can_follower_r` → port 1234（右）、`can_follower_l` → port 1235（左）** を、グリッパ `linear_4310` で起動する [^setup-servers]。表示されたportと実機の左右が一致するか確認し、勝手に入れ替えないこと。グリッパ型式が異なる場合はこのスクリプトの修正が必要になる（変更点を記録する）。

### 9.3 非駆動の検証が終わるまでは実行禁止：robot client

> [!CAUTION]
> 下記は**参考Gistを1台構成へ置換した実機駆動用コマンド例**。`robot_client`はactionを**ロボットへ実際に送る**。**G4（実観測からactionをログし値を確認する非駆動試験）と担当教員の承認が終わるまで、実行しないこと。** 非駆動試験にはリポジトリの `scripts/dry_run_no_actuation.py` を使う（§8 の FastAPI backend、または robot_client の action 送信をログ専用に差し替えた LeRobot backend）。

以下の3つのSerial値は**必ず実機で取得したものに置換**する。参考Gistのサンプルserialをコピーしないこと。カメラprofile（`640×360@30`）も実機対応を確認すること。

```bash
# Terminal R2: ★安全承認後のみ実行★
cd ~/molmoact2-setup/workspaces/lerobot-client
source .venv/bin/activate

python -m lerobot.async_inference.robot_client \
  --server_address 127.0.0.1:8000 \
  --robot.type bi_yam_follower \
  --robot.left_arm_port 1235 \
  --robot.right_arm_port 1234 \
  --robot.cameras '{
    right: {"type": "intelrealsense", "serial_number_or_name": "REPLACE_WITH_D405_RIGHT_SERIAL", "width": 640, "height": 360, "fps": 30},
    left:  {"type": "intelrealsense", "serial_number_or_name": "REPLACE_WITH_D405_LEFT_SERIAL",  "width": 640, "height": 360, "fps": 30},
    top:   {"type": "intelrealsense", "serial_number_or_name": "REPLACE_WITH_D435_TOP_SERIAL",   "width": 640, "height": 360, "fps": 30}
  }' \
  --task "Pick up the object and place it on the side." \
  --policy_type molmoact2 \
  --pretrained_name_or_path="" \
  --policy_device cuda \
  --actions_per_chunk 30 \
  --chunk_size_threshold 0.0 \
  --aggregate_fn_name weighted_average \
  --debug_visualize_queue_size True \
  --policy_config_overrides '[
    "--checkpoint_path=allenai/MolmoAct2-BimanualYAM",
    "--norm_tag=yam_dual_molmoact2",
    "--inference_action_mode=continuous",
    "--normalize_gripper=false"
  ]'
```

上記フラグは Gist と一致することを確認済み（`--server_address` は Gist では `<SERVER_IP>:8000`）[^gist]。`bi_yam_follower` は `left_arm_port=1235` / `right_arm_port=1234` が既定 [^setup-servers]。MolmoAct2 は **absolute joint-pose 制御**で、continuous action mode が推奨 [^hf-card]。

**注**：forkやLeRobot本家でCLI/APIが更新されている場合は、**使っているcommitの`--help`と実装**に合わせて修正する。

### 9.4 非同期実行での測定項目

- 推論時間：平均、中央値、p95、最大値、初回ロード後のwarm-upを分離
- 実行側のfps、action chunkの長さ、queue size（`--debug_visualize_queue_size True`）
- observationの取得からactuationまでの遅延、古いactionが適用されていないか
- カメラ切断/CAN error/timeouts/安全停止の有無
- GPU 推論とカメラ処理と CAN ループが同じ機体で走るため、CAN ループのジッタ（周期のばらつき）も記録する

**速度の見込み**：MolmoAct2 は Qwen3-4B backbone + DiT action expert で、公称 55.8 Hz（H100、CUDA Graph 使用）[^molmoact-readme]。Spark はメモリ帯域 273 GB/s（H100 の約 1/12）で、同クラスの Jetson Thor では π0 が TensorRT 最適化込みで約 22 Hz という報告 [^thor]。素の PyTorch bf16 では **100〜300 ms / 推論（3〜10 Hz）程度**と見込む（未実測）。1 秒 chunk（30 step）の非同期実行なら 30 Hz ループは成立しうるが、**33 ms 以内の反応は期待しない**。action queueが枯渇する場合は安全に停止し、§1 のフォールバック構成を検討する。

---

## 10. 安全確認・実機評価への移行

### Gate G4（ここまでの結果を教員に報告）

- [x] DGX SparkのOS/architecture/GPU情報を保存（2026-09-30、`logs/raw/system-info-2026-09-30.txt`）
- [x] YAM左右のstateを取得（位置指令なし）（2026-09-30、`logs/gate-G1.md`）
- [x] D435(top)・D405(left/right)の同時RGB取得とserial role対応を保存（2026-09-30、`logs/gate-G2.md`）
- [x] MolmoAct2-BimanualYAMのモデルロード成功（2026-09-30、`logs/gate-G3.md`）
- [x] **モータ指令を無効化した状態**で、実RGB ×3と実14D state → action chunkを生成（2026-09-30、30 クエリ、`logs/gate-G4.md`）
- [x] action shape、単位・左右順序・gripper値、NaN/Inf、各関節の上下限を検査（2026-09-30：(30,14)、rad、left→right、gripper 0.98〜1.0、NaN 0。関節上下限は未設定のため未検査）
- [x] actionの時系列グラフと、現在stateからの差分を提出（`logs/g4-real-actions-2026-09-30.png`、`logs/gate-G4.md`）
- [x] latencyを記録（2026-09-30：往復 median 444 ms ≈ 2.25 Hz）。queue 挙動は LeRobot async 経路（8000）で未計測

**ここで一度レビューを受け、G5への進行承認を得ること。**

### Gate G5（承認後）

**【2026-09-30 教員承認済み】** 実施手順は `docs/setup-runbook.md` §3.7、設計と閾値は `logs/gate-G5.md` と `dryrun/safety.py`。実行は `scripts/run_policy_guarded.py`（direction-test → hold → shadow → run）。

1. 左右YAMの制御方向・gripper方向を再確認する。
2. 最大ステップ変位／速度／workspace／衝突を制限する実行側safety filterを入れる。
3. **モータ timeout の方針を決める**。既定の 400 ms のままだと制御ループの一瞬の停滞で腕が damping モードに落ちて崩れる [^yam-ref]。無効化する場合は、(a) バスを使う全プログラムを止め、(b) `python i2rt/motor_config_tool/set_timeout.py --channel can_follower_l` を**2 回**実行（右腕も同様）、(c) 以降は `get_yam_robot(..., zero_gravity_mode=False)` のように **PD 目標を持たせて初期化**し、重力補償ループの失敗が無制御トルクにならないようにする [^i2rt-readme]。再有効化は同スクリプトの `--timeout`。**この作業は教員承認とペアで行う**。
4. まず把持物なし・低速・短時間で試す。安全監視者をつける。
5. training distributionに近い簡単な物体操作から開始する。
6. 試行毎に動画、task instruction、画像、state/action、成功・失敗、停止理由を保存。
7. 標準グリッパで再現できた後にFlexPoint適応、GELLO YAM Activeによるデータ収集・fine-tuningへ進む。

---

## 11. トラブルシューティング

| 症状 | 最初に見る場所 | 次の対応 |
|---|---|---|
| `can*`が出ない | `modinfo gs_usb`, `ip link`, `lsusb`, CANableケーブル/firmware | モジュールが無ければ `linux-modules-extra`。CANableを1本ずつ、別USBポートで試す |
| `can0`/`can1`の左右が逆 | 片腕ずつ接続して serial を記録 | udev で `can_follower_l/r` に固定し、通電前に再確認 |
| 再起動後に CAN が使えない | bitrate 未設定 | `reset_all_can.sh` または `ip link set ... bitrate 1000000` を再実行 |
| YAMが急動作・異常振動・突然脱力 | 指令値・重力補償・CAN・400 ms timeout | **直ちに安全停止**。教員確認まで再開しない |
| D405が見えない | 端子がUSB 3.0 Micro-Bか、`lsusb -t`、`rs-enumerate-devices` が 0 台なら backend | 1台だけ・短いケーブル・別portで試す。起動後に挿し直す |
| `lsusb -t` が 480M | 起動前から挿していた | 抜き差しする |
| 3台同時にfps低下 / `dmesg` に xHCI エラー | `lsusb -t`, 解像度/fps, hub帯域 | 解像度/fps を下げる。Spark 直結の台数を増やす。librealsense を新しくする |
| `uv sync`失敗 | `uname -m`, Python/lockfile, wheel対応 | ARM64依存問題を記録。古いx86 wheelを強制導入しない。sapien なら §8.1 の回避案 |
| `torch.cuda.is_available()` が False | `torch.__version__` が 2.10 以前 | 2.11 以上の aarch64 wheel に揃える |
| `nvrtc: error: invalid value for --gpu-architecture` / `/act` が 500 | torch が `+cu128` | §5 の手順で `+cu130` に差し替え、`uv run --no-sync` で起動（2026-09-30 実証） |
| `uv sync` が `mplib` / `toppra` / `sapien` で失敗 | aarch64 wheel 無し | `--no-install-package` で 4 つ除外（§8.1、2026-09-30 実証） |
| `ruckig` のビルド失敗（`Use build.targets instead of cmake.targets`） | scikit-build-core >= 0.10 | `--build-constraints`（`scikit-build-core<0.10`）を付ける（§6.2、2026-09-30 実証） |
| CUDA modelロード失敗 | `nvidia-smi`、torchのversion/arch、`libcudart.so.12` エラー | CUDA 12 前提の拡張を外す |
| サーバ接続失敗 | `127.0.0.1:8000`、server log | **FastAPI 8202とasync gRPC 8000を混同しない** |
| camera roleが逆 | 保存した3画像/serial | `top,left,right`の対応と設定を修正 |
| queueが空になる | 推論latency、fps、chunk長 | ロボットを安全停止して設定見直し。必要なら §1 のフォールバック構成 |
| model actionが異常 | 正規化tag、14D順序、グリッパ型 | **実機へ送信しない**。参照実装と照合 |

---

## 12. 提出物と完了条件

### 最初の報告（G0–G2）

- 配線全景・左右YAM・各カメラ配置の写真
- YAMおよびCANableの型式、グリッパ型式、24 V 電源の同梱有無
- OS/GPUの確認結果（`system-info.txt`、`modinfo gs_usb` の結果を含む）
- `ip link`/`lsusb -t`の結果、左右CANの対応（udev rules の内容）
- YAM左右stateのログ（位置指令なし）
- D435/D405 ×2のserial一覧とRGB画像3枚、同時取得の結果（`dmesg` の抜粋）

### 第二報告（G3–G4）

- Git commit ID（client/server/I2RT/official）とPython/torch/CUDAのversion
- インストール成功/失敗箇所、エラーログと解決策（sapien / rpi-lgpio / pyrealsense2 の扱いを含む）
- checkpointロード、モデル推論latency
- RGB ×3 + state 14D → action chunkの**非駆動試験ログ**
- action shape・左右順・限界値チェック結果
- G5で提案する安全制限と停止手段（timeout 方針を含む）

### GitHub Issueの例

- `01 Hardware assembly and USB connection`
- `02 YAM CAN and read-only state tests`
- `03 RealSense three-camera test`
- `04 DGX Spark / ARM64 dependencies`
- `05 MolmoAct2 dummy inference`
- `06 Real-observation dry run (NO ACTUATION)`
- `07 Safety review and low-speed deployment`

ログや写真は実験データ用保存場所に置き、**モデルcheckpoint、巨大動画、認証token、個人情報はリポジトリへcommitしない**。手順修正・問題解決はREADMEへのPull RequestまたはIssueとして残す。

---

## 13. 参考リンク

### Primary references

- [MolmoAct2 official repository](https://github.com/allenai/molmoact2)
- [MolmoAct2-BimanualYAM checkpoint](https://huggingface.co/allenai/MolmoAct2-BimanualYAM)
- [MolmoAct2 paper (arXiv:2605.02881)](https://arxiv.org/abs/2605.02881)
- [I2RT SDK (i2rt-robotics/i2rt)](https://github.com/i2rt-robotics/i2rt)
- [I2RT YAM documentation](https://doc.i2rt.com/products/yam)
- [I2RT Research Kit](https://i2rt.com/products/molmoact-2-research-kit)
- [YAM reference implementation (williamtsai726/YAM)](https://github.com/williamtsai726/YAM)
- [Async inference example / SuveenE Gist](https://gist.github.com/SuveenE/6bc2b822ac44807565729c2b0ebb1cb2)
- [SuveenE/lerobot fork](https://github.com/SuveenE/lerobot)
- [LeRobot camera identification](https://huggingface.co/docs/lerobot/cameras)
- [LeRobot async inference documentation](https://huggingface.co/docs/lerobot/async)

### Hardware references

- [DGX Spark hardware overview](https://docs.nvidia.com/dgx/dgx-spark/hardware.html)
- [DGX Spark release notes](https://docs.nvidia.com/dgx/dgx-spark/release-notes.html)
- [Anker A8308 official specification](https://www.ankerjapan.com/products/a8308)
- [candleLight firmware (gs_usb)](https://github.com/candle-usb/candleLight_fw)

### DGX Spark / aarch64 に関する実機報告（2026-09-29 時点）

- [PyTorch forum: DGX Spark GB10 sm_121 support](https://discuss.pytorch.org/t/dgx-spark-gb10-cuda-13-0-python-3-12-sm-121/223744)
- [HF blog: Isaac teleop and GR00T in LeRobot (Spark 向け torch install 例)](https://huggingface.co/blog/nvidia/nvidia-isaac-teleop-and-gr00t17-in-lerobot)
- [NVIDIA forum: RealSense cameras on DGX Spark](https://forums.developer.nvidia.com/t/dgx-spark-and-realsense-cameras-interfacing/381443)
- [NVIDIA forum: DGX Spark USB port observations](https://forums.developer.nvidia.com/t/some-observation-about-dgx-spark-usb-ports/359661)
- [David-Martel: GB10 RealSense findings](https://raw.githubusercontent.com/David-Martel/DGX-Spark/main/docs/GB10_REALSENSE_FINDINGS.md)
- [Jetson AI Lab: openpi on Thor (latency reference)](https://www.jetson-ai-lab.com/tutorials/openpi_on_thor/)

### Future work (今回の初期評価には不要)

- GELLO YAM Active: <https://github.com/wuphilipp/gello_mechanical/tree/main/yam/yam_active_gello>
- FlexPoint: <https://i2rt.com/products/flexpoint-adaptive-gripper-by-i2rt-robotics>

---

## 14. 検証履歴

| 日付 | 内容 | 担当 |
|---|---|---|
| 2026-09-28 | 初版作成 | |
| 2026-09-30 | Spark 初回セットアップ完了（Ubuntu 24.04.5 / driver 580.178 / CUDA 13.0）。3 環境構築、checkpoint 取得、公式 FastAPI server でダミー推論成功（G3）。cu130 差し替え・除外パッケージ・build constraint を追記 | Claude Code + 大田 |
| 2026-09-29 | 上流ソースと照合（Gist、SuveenE/lerobot 両 branch、allenai/molmoact2、i2rt SDK、doc.i2rt.com、DGX Spark 実機報告）。§3.2 配線変更、§3.3 CAN 名変更、§6.3 read-only 表現修正、§8 二段構え化、aarch64 固有事項追記 | Claude Code + 大田 |

**更新ルール**：動作確認が取れたコマンドの横に、**確認日・実機環境・commit ID・担当者**を追記する。未検証の記述を黙って「動作確認済み」に変更しない。

[^kit]: I2RT, "MolmoAct 2 Research Kit" 製品ページ（2026-09-29 取得）。同梱：YAM Standard ×2、2060 アルミプロファイル 80 cm、伸縮式カメラデスクマウント、特大 G クランプ ×2。別売：D435 ×1（overhead）、D405 ×2（wrist）。CANable・電源・グリッパの記載なし。
[^yamdoc]: doc.i2rt.com/products/yam（2026-09-29 取得）。CAN 1 Mbit/s、DM4340（肩）/ DM4310（肘・手首）、CANable と 24 V 電源が必要。
[^i2rt-readme]: github.com/i2rt-robotics/i2rt README および `i2rt/robots/get_robot.py`, `motor_chain_robot.py`, `scripts/reset_all_can.sh`, `pyproject.toml`（main、2026-09-29 取得）。
[^i2rt-udev]: 同リポジトリ `docs/guides/set-persistent-can-ids.md`（2026-09-29 取得）。candlelight firmware、udev `ATTRS{serial}` による固定名。
[^yam-ref]: github.com/williamtsai726/YAM README（2026-09-29 取得）。400 ms timeout が長時間の teleop / 評価で "abrupt collapse" を起こすため `set_timeout.py` を左右で実行、再接続後に `reset_all_can.sh`。
[^gist]: gist.github.com/SuveenE/6bc2b822ac44807565729c2b0ebb1cb2 "Running MolmoAct2 on bimanual YAM arms (async inference)"（最終更新 2026-06-02、2026-09-29 取得）。
[^client-pyproject]: github.com/SuveenE/lerobot `bimanual-yam-arms-support` branch の `pyproject.toml`, `.gitmodules`（2026-09-29 取得）。
[^server-pyproject]: 同 `molmoact2-yam-async-inference-server` branch の `pyproject.toml`, `uv.lock`（2026-09-29 取得）。
[^setup-servers]: 同 `bimanual-yam-arms-support` branch の `src/lerobot/scripts/setup_bi_yam_servers.py` および `src/lerobot/robots/bi_yam_follower/config_bi_yam_follower.py`（2026-09-29 取得）。
[^molmoact-readme]: github.com/allenai/molmoact2 README（main、2026-09-29 取得）。
[^molmoact-pyproject]: 同 `pyproject.toml`（2026-09-29 取得）。
[^host-server]: 同 `examples/yam/host_server_yam.py`（2026-09-29 取得）。
[^molmoact-client]: 同 `examples/yam/molmoact_client.py`（2026-09-29 取得）。
[^yam-yaml]: 同 `examples/yam/configs/yam_left.yaml`, `gello_min/robot.py`, `gello_min/yam.py`（2026-09-29 取得）。状態は left(7) → right(7) の順、グリッパ初期値 1.0、request timeout 500 ms、frame age limit 0.5 s。
[^hf-card]: huggingface.co/allenai/MolmoAct2-BimanualYAM model card および HF API（gated: false、5 shards 約 29.1 GB、2026-09-29 取得）。
[^spark-hw]: docs.nvidia.com/dgx/dgx-spark/hardware.html（2026-09-29 取得）。
[^spark-usb]: NVIDIA Developer Forum "DGX Spark USB ports are USB 4?"（2026-09-29 取得）。実測 USB 3.2 Gen 2x2。
[^spark-usb2]: NVIDIA Developer Forum "Some observation about DGX Spark USB ports"（2026-09-29 取得）。
[^spark-rs]: David-Martel/DGX-Spark `docs/GB10_REALSENSE_FINDINGS.md` および David-Martel/librealsense PR #16（2026-09-29 取得）。
[^torch-spark]: PyPI torch 2.9〜2.14 の aarch64 wheel メタデータ、PyTorch forum "DGX Spark GB10 sm_121"、HF blog "Isaac teleop and GR00T in LeRobot"（2026-09-29 取得）。
[^thor]: NVIDIA Developer Forum "Real-time inference on Thor / RTX: pi0.5, GR00T"、Jetson AI Lab "openpi on Thor"（2026-09-29 取得）。
