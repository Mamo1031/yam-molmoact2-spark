# Gate G0

| 項目 | 値 |
|---|---|
| 日付 | |
| 担当 / 監視 | |
| Spark OS / driver / CUDA | |
| 使用 commit（client / server / i2rt / official） | |

## 実行したこと
- 2026-09-30: 配線マニュアル `docs/hardware-wiring.md` を作成（公式写真 13 枚を `docs/images/` に保存）。I2RT に単体アームの配線図は無く、YAM Box 写真と Damiao 標準構成からの推定を含む
- 2026-09-29: グリッパは `linear_4310` の見込み（ユーザー申告、現物未確認）。SDK 既定値と一致するため、確認できれば `setup_bi_yam_servers.py` の変更は不要
- 2026-09-29: Spark は未通電。初回起動から着手

## 結果（貼り付けたログの要約）
- 2026-09-30 写真 1〜5（`logs/photos-2026-09-30/`）：電源 24 V / 14 A ×2（XT30 出力）、CANable USB-C ×2（WAGO 端子、2×2 ジャンパ）、I2RT 分岐ハーネス（黒 4 ピン ↔ 黄 XT30 + CANable）、グリッパは linear 型（linear_4310 想定）、両アームはフレーム固定済み。E-stop なし

## 問題と対応
- 2026-09-29: 初回起動ウィザードが "plug in a USB mouse or put a bluetooth mouse into pairing mode" で停止（キーボード・マウスは Anker ハブ経由、LAN 接続済み）。
  - 公式 first-boot ページ：この画面はキーボード/マウス未検出時に出る。USB 機器は「いつ挿しても動くはず」。Bluetooth ペアリングは Get Started 画面でのみ可。https://docs.nvidia.com/dgx/dgx-spark/first-boot.html
  - 対応順：マウスをハブから外し Spark 本体の USB-C（左端の電源ポート以外）へ直挿し → 有線シンプルマウスに替える → 別 PC から Wi-Fi ホットスポット（Quick Start Guide のシール記載 SSID）経由でブラウザ設定 → 効かなければ全部直結して電源入れ直し
  - 禁止：ダウンロード/インストール開始後の電源断（USB コントローラの firmware 破損報告あり）
  - 2026-09-29 23:57 Mac から調査：`spark-cf69.local` → 192.168.1.161（有線 LAN 側、mDNS 解決・ping OK）。開いているのは **22/tcp（OpenSSH 9.6p1 Ubuntu 24.04、publickey/password）のみ**。80/443 は閉。mDNS 広告も `_ssh._tcp` のみ → **設定用 Web ポータルは LAN 側には出ていない**
  - 同時刻、Wi-Fi ホットスポット **`spark-cf69`**（5 GHz ch36、-41 dBm、WPA2/WPA3）を Mac から検出 → ポータルはホットスポット側でのみ提供されている見込み
  - Mac の上り回線は Wi-Fi（Buffalo-G-4AC0）のみのため、Mac をホットスポットに繋ぐと作業セッションが切れる。別端末（スマホ）か、Mac を有線化してから Wi-Fi をホットスポットに切替える
  - 結果：（記入待ち）

## 次へ進む条件の充足
- [ ] （reference.md §1 の条件を転記）
