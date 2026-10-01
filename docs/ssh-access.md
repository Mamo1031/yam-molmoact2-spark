# Spark に自分の PC から SSH で入る

対象：研究室メンバー。Spark の `murata-lab` アカウントに、自分の PC から鍵で入れるようにする。作成 2026-09-30。

前提：自分の PC が Spark と同じ LAN（192.168.1.x）につながっていること。別のネットワークからは入れない。

## 1. 自分の PC で鍵を作る

macOS / Linux はターミナル、Windows は PowerShell で実行する。既に `~/.ssh/id_ed25519` がある人は作り直さない。

```bash
ssh-keygen -t ed25519 -C "alice@alice-laptop"    # コメントは半角英数で、誰の PC か分かる名前に
```

公開鍵（1 行）を表示し、既に Spark に入れる人へ送る。送るのは `.pub` の中身だけ。

```bash
cat ~/.ssh/id_ed25519.pub                                          # macOS / Linux
Get-Content -Encoding UTF8 $env:USERPROFILE\.ssh\id_ed25519.pub    # Windows
```

## 2. 既に入れる人が、公開鍵を Spark に登録する

既に Spark に入れる Mac / Linux で実行する。受け取った 1 行を `newpc.pub` に保存してから登録する。

```bash
pbpaste > newpc.pub                     # macOS：1 行をコピーした状態で実行（Linux はエディタで保存）
ssh-keygen -lf newpc.pub                # fingerprint が 1 行出れば正しい公開鍵
ssh-copy-id -f -i newpc.pub murata-lab@spark-cf69.local
```

## 3. 自分の PC から接続する

```bash
ssh murata-lab@spark-cf69.local
```

初回だけホスト鍵の確認が出る。表示が次のどちらかと一致したら `yes` と答える。

- ED25519：`SHA256:YT7DxbMuiqEtnnYJ05MFaGwvjfTml3ffhsiOfVM+9RY`
- ECDSA：`SHA256:dFIOFYEPSBHQS/Np+s5TDy4iJv4j6M/cXV+1UgfXcxY`

`ssh spark` だけで入りたい場合は、`~/.ssh/config`（Windows は `C:\Users\<名前>\.ssh\config`）に追記する。

```text
Host spark
    HostName spark-cf69.local
    User murata-lab
    IdentityFile ~/.ssh/id_ed25519
    IdentitiesOnly yes
```

## つながらないとき

| 症状 | 対処 |
|---|---|
| `Could not resolve hostname` | `spark-cf69.local` の代わりに IP を使う：`ssh murata-lab@192.168.1.161`（IP は変わることがある。Spark 上で `hostname -I` で確認） |
| タイムアウトする | PC が Spark と同じ LAN にいるか確認する（別の Wi-Fi やゲスト用ネットワークからは届かない） |
| `Permission denied (publickey)` | 手順 2 の登録ができていない。`ssh -v murata-lab@spark-cf69.local` で、どの鍵を出しているか確認する |

## 4. ルータなしで Mac と Spark を直結する（会場向け）

Spark の有線ポート（`enP7s7`）と Mac の USB-C イーサネットアダプタをケーブルで直結すれば、Wi-Fi もルータも無しで `ssh spark` が通る。推論は Spark 内で完結するので、インターネットも不要（方策サーバは `~/molmoact2-setup/start_policy_server.sh` が `HF_HUB_OFFLINE=1` でローカルキャッシュから起動する）。

**一度だけの設定（Spark、sudo が要る）**：DHCP が無いときに自動アドレス（169.254.x.x）も付くようにする。
```bash
ssh -t spark 'sudo nmcli con modify "有線接続 3" ipv4.link-local enabled && sudo nmcli con up "有線接続 3"'
```
Mac 側は既定の DHCP のままでよい（自動的に 169.254.x.x になる）。名前解決は mDNS（`spark-cf69.local`）がケーブル上でも効く。

**つなぎ方**：Spark のケーブルをルータから外して Mac のアダプタに挿す → 10〜20 秒待つ → `ssh spark`。
研究室に戻ったらケーブルをルータに戻すだけ（DHCP のアドレスに戻る。設定変更は不要）。

確認：Mac で `ifconfig en3 | grep inet`（169.254.x.x が付く）、`dns-sd -G v4 spark-cf69.local`。
