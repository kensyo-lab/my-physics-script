# my-physics-script
Cosmos：SPICE Kernel Downloader
# SPICE Kernel Downloader GUI

Apache Index 形式の HTTP サーバーから、ディレクトリ階層を維持して  
SPICE カーネルファイルを一括ダウンロードするための GUI ツールです。

ESA の `spiftp.esac.esa.int` を主な対象として開発しましたが、  
Apache Directory Index を持つ任意の HTTP サーバーに対応しています。

---

## スクリーンショット

```
◈ SPICE KERNEL DOWNLOADER                              RUNNING
────────────────────────────────────────────────────────────
URL      [ http://spiftp.esac.esa.int/data/SPICE/VENUS-EXPRESS/ ]
保存先   [ ./VENUS-EXPRESS                               ] […]
拡張子   [                    ] 空=全件  例: .bc .bsp .tls

並列数 [2]  タイムアウト(秒) [60]  リトライ回数 [3]  ☑ 既存スキップ

TOTAL  1024 / 5299  (19.3%)
████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
NOW
░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  vex_sc_20140101.bc

  OK        SKIP      ERROR
  376        648         1

▶ START  ■ STOP  ↺ RESUME  ⟳ RETRY ERR  🗑 LOG CLEAR

LOG                                              💾 SAVE LOG
10:24:31  [SYSTEM] ── Phase 2: ダウンロード開始 ──
10:24:32  [OK]    vex_sc_20131201.bc
10:24:33  [ERROR] vex_ro_2014.bc → HTTPError: HTTP Error 404: Not Found
```

---

## 特徴

- **階層維持ダウンロード** — サーバー上のディレクトリ構造をそのままローカルに再現
- **任意 URL 対応** — VENUS-EXPRESS に限らず、Apache Index を持つサーバーなら何でも使用可
- **ストリーム書き込み** — 1 MB チャンク単位で書き込み、GB 級ファイルでもメモリを圧迫しない
- **中断・再開** — 既存ファイルをスキップして中断箇所から再開可能
- **エラー再試行** — 失敗したファイルのみを選択的に再ダウンロード
- **プログレス表示** — 全体進捗バー・現在ファイル・件数・パーセント表示
- **拡張子フィルタ** — 必要なカーネル種別だけを選択してダウンロード可能
- **ログ保存** — ダウンロード履歴をタイムスタンプ付きテキストで保存
- **スリープ抑制** — macOS では `caffeinate` を自動起動し、長時間ダウンロード中のスリープを防止
- **完了 BEEP** — ダウンロード完了時に音で通知（Windows / macOS / Linux 対応）
- **標準ライブラリのみ** — 追加パッケージ不要、Python 3.8+ で動作

---

## 動作環境

| 項目 | 要件 |
|------|------|
| Python | 3.8 以上 |
| 依存ライブラリ | 標準ライブラリのみ（`tkinter` 含む） |
| OS | Windows / macOS / Linux |

> **Linux で tkinter が見つからない場合:**
> ```bash
> # Ubuntu / Debian
> sudo apt install python3-tk
>
> # Fedora / RHEL
> sudo dnf install python3-tkinter
> ```

---

## インストール

```bash
git clone https://github.com/kensyo-lab/my-physics-script.git
cd my-physics-script
python spice_downloader_gui.py
```

追加インストールは不要です。

---

## 使い方

### 基本的なダウンロード手順

1. **URL** 欄にダウンロード元の Apache Index URL を入力
2. **保存先** 欄にローカルの保存ディレクトリを指定（`…` ボタンでダイアログ選択も可）
3. **▶ START** ボタンを押す

スキャンフェーズ（ディレクトリ再帰走査）が終わり次第、並列ダウンロードが開始されます。

### 設定項目

| 項目 | 説明 | 既定値 |
|------|------|--------|
| URL | ダウンロード元の Apache Index URL | ESA VENUS-EXPRESS |
| 保存先 | ローカルの保存先ディレクトリ | `./VENUS-EXPRESS` |
| 拡張子フィルタ | スペース区切りで対象拡張子を指定。空欄=全件取得 | 空欄（全件） |
| 並列数 | 同時ダウンロード数（1〜8） | 2 |
| タイムアウト | HTTP タイムアウト秒数（10〜300） | 60 秒 |
| リトライ回数 | 失敗時の再試行回数（0=1回のみ試行） | 3 |
| 既存スキップ | サイズ > 0 の既存ファイルをスキップ | ON |

> **並列数について:** 3 以上に設定するとサーバ負荷に関する警告が表示されます。  
> ESA サーバを相手にする場合は **2〜3 が安定**しています。

### 拡張子フィルタの使い方

SPICE カーネルの種別ごとに拡張子が決まっています。  
必要なファイルだけを取得したい場合は、フィルタ欄に半角スペース区切りで指定してください。

```
.bc .bsp .tls .tf .ti .tpc .tsc .tm
```

| 拡張子 | カーネル種別 |
|--------|-------------|
| `.bc` | CK（姿勢カーネル） |
| `.bsp` | SPK（軌道カーネル） |
| `.tls` | LSK（うるう秒カーネル） |
| `.tf` | FK（フレームカーネル） |
| `.ti` | IK（機器カーネル） |
| `.tpc` | PCK（惑星定数カーネル） |
| `.tsc` | SCLK（宇宙機クロックカーネル） |
| `.tm` | MK（メタカーネル） |

### 中断と再開

- **■ STOP** — ダウンロードを安全に中断します。進行中のチャンクを書き終えてから停止し、未完了の `.tmp` ファイルを自動削除します
- **↺ RESUME** — 既存スキップを利用して中断箇所から再開します。ダウンロード済みファイルはスキップされます

### エラー再試行

- **⟳ RETRY ERR** — 失敗したファイルのみを再ダウンロードします。成功済みファイルには触れません
- 失敗した URL は保存先ディレクトリの `failed_downloads.txt` にも自動保存されます

### ログ

- ログエリアにはリアルタイムでダウンロード状況が表示されます
- **💾 SAVE LOG** でログをテキストファイルに保存できます（ファイル名にタイムスタンプが付きます）
- **🗑 LOG CLEAR** でログ表示を消去します（ファイルへの影響はありません）

---

## macOS スリープ抑制機能

macOS では、ダウンロード開始時に `caffeinate -i` を自動起動し、  
ダウンロード中にシステムがスリープするのを防ぎます。

- ダウンロード完了・中断・ウィンドウを閉じると自動的に解除されます
- `caffeinate` が見つからない場合は `[WARN]` をログに出力してダウンロードを続行します
- Windows / Linux では何も実行しません

---

## ファイル構成

```
my-physics-script/
├── spice_downloader_gui.py   # メインスクリプト（これだけで動作）
├── README.md
└── download_spice_kernels.py # CUIバージョン（GUI不要な環境向け）
```

---

## CUI 版について

GUI なしのコマンドライン版として `download_spice_kernels.py` も同梱しています。  
SSH 環境やヘッドレスサーバーで使用する場合はこちらをご利用ください。

```bash
# スクリプト冒頭の定数を編集してから実行
python download_spice_kernels.py
```

設定はスクリプト冒頭の定数を直接編集します。

| 定数 | 説明 | 既定値 |
|------|------|--------|
| `BASE_URL` | ダウンロード元 URL | ESA VENUS-EXPRESS |
| `OUTPUT_DIR` | 保存先ディレクトリ | `./VENUS-EXPRESS` |
| `NUM_WORKERS` | 並列ダウンロード数 | `2` |
| `RETRY_MAX` | 最大リトライ回数 | `3` |
| `TIMEOUT` | タイムアウト秒数 | `60` |
| `ALLOWED_EXTS` | 拡張子フィルタ（`None` で全件） | `None` |

---

## 対応サーバー

Apache / nginx の Directory Index (`Index of /`) 形式を返す HTTP サーバーであれば動作します。

動作確認済み:

- `http://spiftp.esac.esa.int/data/SPICE/` （ESA SPICE カーネルサーバー）

---

## 注意事項

- 大量のファイルを短時間で取得するとサーバーに負荷をかける場合があります。**並列数は 2〜3 程度**を推奨します
- ESA のデータは公開されていますが、利用規約を確認の上ご使用ください
- ダウンロード中はネットワーク帯域を継続的に使用します

---

## ライセンス

MIT License

---

## 作者

けんしょう  
ニコニコ動画にて宇宙・物理系の教育動画を制作しています。  
このツールはあかつき（Venus Climate Orbiter）の解説動画制作に使用するため開発しました。
