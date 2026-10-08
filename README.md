# head3Dv1 for macOS

[dicom-merge-tool](https://github.com/Jin-hiroo/dicom-merge-tool)（head3Dv1 — DICOM 結合支援ツール）を、
ダブルクリックで起動できる macOS アプリ **`head3Dv1.app`** にするためのリポジトリです。

**元のリポジトリとコードには一切手を加えていません。** 元コードは `upstream/` に
git サブモジュールとして（コミットを固定して）読み込み、そのままアプリに同梱します。

> **本ソフトウェアは診断用医療機器ではありません。**
> 研究・造形補助を目的としたツールであり、臨床診断には使用しないでください。
> 患者データはすべてこの Mac 内でのみ処理され、外部送信は一切行いません。

使い方（読込 → 位置合わせ → 結合 → 書出し）は
[元リポジトリの README](https://github.com/Jin-hiroo/dicom-merge-tool#使い方) を参照してください。
アプリ版でも操作はまったく同じです。

## 動作環境

- Apple Silicon (M1 / M2 / M3 …) の Mac
- macOS 14 Sonoma 以降（GitHub Actions で作った DMG の場合。同梱の SciPy が macOS 14 向けのため）
  - 対応する最小 macOS は、同梱したライブラリが要求する版から自動で決まり、Info.plist に書き込まれます。
    macOS 13 以前の Mac で自分でビルドすると、その Mac 向けのライブラリが選ばれるため、その Mac で動きます
- メモリ 16 GB 以上を推奨（元ツールと同じ）

## インストール

1. `head3Dv1-<バージョン>-arm64.dmg` を開く
   （GitHub の [Actions](../../actions) の成果物 `head3Dv1-arm64`、またはタグを打った場合は Releases から入手。
   自分でビルドした場合は `dist/` にあります）
2. `head3Dv1` を `Applications` フォルダへドラッグ
3. Launchpad または `/Applications/head3Dv1.app` から起動

### 初回起動で「開けません」と出た場合

このアプリは Apple の Developer ID で署名・公証していない（アドホック署名のみ）ため、
**インターネットからダウンロードした DMG** から入れた場合は初回だけ macOS に止められます。
自分の Mac で `./build.sh` してできたアプリではこの操作は不要です。

- **システム設定 → プライバシーとセキュリティ** を開き、下の方の
  「"head3Dv1" は開発元を確認できないため…」の横の **［このまま開く］** を押す
- それでも「壊れているため開けません」と出る場合はターミナルで:

  ```bash
  xattr -dr com.apple.quarantine /Applications/head3Dv1.app
  ```

DICOM をデスクトップ・書類・USB / CD などから読むときに、macOS がフォルダへの
アクセス許可を求めることがあります。［許可］してください。

## データの保存場所

アプリの中には何も書き込みません（署名が壊れる・書込不可の場所に置かれることがあるため）。

| 内容 | 場所 |
|---|---|
| 作業用ボリューム（memmap、数 GB になることがある） | `~/Library/Application Support/head3Dv1/scratch/` |
| セッション（自動保存 `_autosave/` と「セッションを保存…」の既定の保存先） | `~/Library/Application Support/head3Dv1/sessions/` |
| ログ | `~/Library/Logs/head3Dv1/head3Dv1.log`（クラッシュ時は `crash.log`） |
| 設定（HU 閾値・ウィンドウ位置など） | `~/Library/Preferences/com.head3dv1.head3Dv1.plist` |

`Application Support` は iCloud で同期されないフォルダなので、患者データが外部に出ることはありません。
Finder で開くには **移動 → フォルダへ移動…** に `~/Library/Application Support/head3Dv1` を入力します。

### 元のツール（`run.py` で起動）との違い

- 作業用ボリュームとセッションの場所が違います。`run.py` 版は元リポジトリ内の
  `scratch/` `sessions/` を使います。`run.py` 版で保存したセッションは
  「ファイル → セッションを開く…」でそのフォルダを指定すればアプリ版でも開けます。
- 設定（QSettings）は両者で共有されます。
- ログがファイルにも残ります。

## 自分でビルドする

必要なもの: Apple Silicon の Mac、Xcode Command Line Tools（`xcode-select --install`）、
**Python 3.12（arm64 版）**。Python は Homebrew（`brew install python@3.12`）か
[python.org](https://www.python.org/downloads/macos/) のものを使ってください。
Anaconda の Python は Qt プラグインが混ざって起動しなくなるため使えません（元 README の注意と同じ）。

```bash
git clone --recursive https://github.com/Jin-hiroo/dicom-merge-tool-mac.git
cd dicom-merge-tool-mac
./build.sh
```

`build.sh` が行うこと:

1. `upstream/`（元コード）が無ければサブモジュールを取得
2. 仮想環境 `.venv-build/` を作り、`upstream/requirements.txt` と `requirements-build.txt` を導入
   （システムの Python には何も入れません）
3. アイコンを生成し、PyInstaller で `dist/head3Dv1.app` を作成
4. **セルフテスト**: 合成 DICOM（元リポジトリの `tests/make_phantom.py`）を
   アプリ自身で読み込み → STL 書き出し → メインウィンドウ表示・3D 描画まで確認
   （一時フォルダで行うのでユーザーのデータや設定には触れません）
5. **バンドル検査**: 全バイナリが arm64 か、最小 macOS が Info.plist の宣言以下か、署名が壊れていないか
6. `dist/head3Dv1-<バージョン>-arm64.dmg` を作成

オプション: `--skip-dmg` / `--skip-selftest` / `--no-gui-test`（ウィンドウを開かない）。
バージョンは `APP_VERSION=1.2.0 ./build.sh` で指定できます。

## 仕組み

```
dicom-merge-tool-mac/
├── upstream/                 元リポジトリ (サブモジュール・無変更)
├── macos/
│   ├── launcher.py           アプリのエントリポイント
│   ├── head3Dv1.spec         PyInstaller の設定 (Info.plist もここ)
│   ├── make_icon.py          アイコン生成
│   └── check_bundle.py       ビルド後のバンドル検査
├── tests/test_launcher.py    書込先差し替えのテスト
├── build.sh                  ビルド一式
└── .github/workflows/        GitHub Actions (macOS arm64 で DMG を作る)
```

元コードは `app/config.py` の位置を基準に `scratch/` と `sessions/` を作るため、
そのままアプリにすると `.app` の中に書き込もうとします。`macos/launcher.py` は
元コードを読み込む前に `app.config.PROJECT_ROOT` と `app.config.SCRATCH_DIR` を
`~/Library/Application Support/head3Dv1/` に向け、ログの出力先を足してから、
元の `app.main.main()` をそのまま呼びます。元のファイルを書き換えたりコピーして
改変したりはしていません。

`app.core.session` は読み込まれた瞬間に保存先を確定するので、この差し替えは
必ずそれより前に行う必要があります。`tests/test_launcher.py` がこの順序を検証しています。

## 元コードを更新する

`upstream/` は特定のコミットに固定しています。元リポジトリの新しい版でアプリを作り直すには:

```bash
git -C upstream fetch origin
git -C upstream checkout origin/main      # または特定のコミット
git add upstream
git commit -m "upstream を更新"
./build.sh
```

アプリに同梱された元コードのコミットは `head3Dv1.app/Contents/Info.plist` の
`Head3DUpstreamCommit` と、起動時のログ 1 行目で確認できます。

## 開発者向け

```bash
# ビルドせずに、元コードをランチャー経由で起動する
.venv-build/bin/python macos/launcher.py

# ランチャーのテスト
.venv-build/bin/python -m pytest tests -q -p no:cacheprovider

# ビルド済みアプリのセルフテストだけを再実行
dist/head3Dv1.app/Contents/MacOS/head3Dv1 --self-test [--dicom <DICOMフォルダ>] [--no-gui]
```

| 環境変数 | 用途 |
|---|---|
| `HEAD3DV1_DATA_DIR` | 作業用ボリューム・セッションの置き場を変える（既定 `~/Library/Application Support/head3Dv1`） |
| `HEAD3DV1_NO_RESTORE=1` | 起動時の復元プロンプトを出さない（元コードの機能） |
| `HEAD3DV1_SETTINGS_SCOPE` | 設定の保存先を切り替える（元コードの機能） |

GitHub Actions（`.github/workflows/build-macos.yml`）は push のたびに macOS (arm64) 上で
`build.sh` を実行し、ランチャーと元リポジトリのテストを流し、`upstream/` が変更されていないことを確かめてから
DMG を成果物として保存します。`v1.0.0` のようなタグを push すると Releases にも DMG が添付されます。

## トラブルシューティング

- **起動しない・すぐ落ちる** — `~/Library/Logs/head3Dv1/head3Dv1.log` と `crash.log` を確認してください。
  ターミナルから `/Applications/head3Dv1.app/Contents/MacOS/head3Dv1` を直接実行するとログがその場に出ます。
- **前回の作業を捨てて起動したい** — `HEAD3DV1_NO_RESTORE=1 /Applications/head3Dv1.app/Contents/MacOS/head3Dv1`
- **ディスクを空けたい** — アプリの「ファイル → 未使用の一時ファイルを削除」、または終了した状態で
  `~/Library/Application Support/head3Dv1/scratch/` を削除（自動保存したセッションは復元できなくなります）。

## ライセンスについて

このリポジトリのビルド用スクリプトは [LICENSE](LICENSE) に従います。作成されるアプリには
元コードのほか PyQt5（GPL v3）、Qt（LGPL v3）、VTK・NumPy・SciPy（BSD）、pydicom（MIT）などが同梱されます。
アプリを第三者に配布する場合は、それぞれのライセンス条件に従ってください。
