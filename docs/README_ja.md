<div align="center">

# 📝 Kirok

**AIエージェントのための永続メモリ**

*覚えて、思い出して、洞察を得る。*

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](../LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org)
[![MCP Compatible](https://img.shields.io/badge/MCP-compatible-green.svg)](https://modelcontextprotocol.io)

**[🇬🇧 English](../README.md)** | 🇯🇵 日本語

</div>

---

Kirok（記録）は、AIアシスタントに**永続的な記憶力**を与える仕組みです。

普通、AIアシスタント（Claude など）は新しい会話を始めるたびに、前の会話の内容をすべて忘れてしまいます。Kirok を導入すると、AIがあなたの好みや過去の判断、学んだことを覚えていてくれるようになります。

## ✨ Kirok でできること

| 機能 | 説明 |
|------|------|
| **🧠 記憶（Retain）** | AIが情報を保存し、重要なポイントを自動で抽出します |
| **🔍 想起（Recall）** | AIが過去の記憶を意味とキーワードの両方で検索します |
| **💡 考察（Reflect）** | AIが蓄積された記憶を分析して、パターンや洞察を生み出します |
| **🔄 重複排除** | 同じような情報が重複して保存されるのを自動で防ぎます |
| **📊 パターン検出** | 記憶から法則やパターンを自動検出します |
| **🎯 テーマ設定** | メモリバンクごとに「何を重視するか」を設定できます |

---

## 🎁 特典：Kirok 専用 Agent Skill 同梱

AIがいち早くKirokの機能を使いこなせるよう、**専用の「Agent Skill」** を同梱しています。

- `kirok`：AI自身にKirokの操作方法とベストプラクティスを教える基本スキルです。いちいち「これを記憶して」と頼まなくても、AIが自律的に記憶を管理するようになります。

**使い方（クイックスタート）**:
1. ダウンロードした `skills` フォルダを、あなたの作業ディレクトリに置きます。
2. チャットの最初に、AIに向かって **「`skills/kirok/SKILL.md` を読んで、その指示に従ってください」** と一言伝えるだけです。
*(💡プロの小技：Claude Desktopなどの「カスタム指示（Custom Instructions）」にこの一文を登録しておけば、毎回自動でプロトコルを読み込んで自律的に動くようになります！)*

---

## 🚀 セットアップ手順（初めての方向け）

上から順番に進めてください。所要時間の目安は **10〜15分** です。

### ステップ 1：Python のインストール

Kirok を動かすには Python 3.12 以上が必要です。

<details>
<summary><b>🍎 Mac の場合</b></summary>

[Homebrew](https://brew.sh/ja/) を使うのが一番簡単です。

```bash
# Homebrew をインストール（初めての方のみ）
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Python をインストール
brew install python@3.12
```

インストール確認：
```bash
python3 --version
# Python 3.12.x 以上が表示されれば OK
```

</details>

<details>
<summary><b>🪟 Windows の場合</b></summary>

1. [python.org/downloads](https://www.python.org/downloads/) にアクセス
2. 最新の Python 3.12 以上のインストーラーをダウンロード
3. **重要**：インストール画面で ✅ **「Add Python to PATH」にチェック** を入れてください
4. 「Install Now」をクリック

インストール確認（PowerShell を開いて）：
```powershell
python --version
# Python 3.12.x 以上が表示されれば OK
```

</details>

### ステップ 2：uv のインストール

[uv](https://docs.astral.sh/uv/) は Python のパッケージ管理ツールです。Kirok のインストールや起動に使います。

<details>
<summary><b>🍎 Mac の場合</b></summary>

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

ターミナルを一度閉じて開き直してから、確認：
```bash
uv --version
```

</details>

<details>
<summary><b>🪟 Windows の場合</b></summary>

PowerShell を開いて実行：
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

PowerShell を**閉じて開き直して**から、確認：
```powershell
uv --version
```

</details>

### ステップ 3：Gemini API キーの取得（無料）

Kirok は Google の Gemini AI を使って記憶を理解・検索します。無料プランで十分に使えます。

1. **[Google AI Studio](https://aistudio.google.com/apikey)** にアクセス
2. Google アカウントでログイン
3. **「Create API Key」**（APIキーを作成）をクリック
4. 表示されるキー（`AIza...` で始まる文字列）をコピー — ステップ 5 で使います

> **💡 ヒント**：無料プランでは1日あたり1,500リクエストまで使えます。通常の使い方なら十分です。

### ステップ 4：Kirok のダウンロードとインストール

<details>
<summary><b>🍎 Mac の場合</b></summary>

```bash
# ホームディレクトリに移動
cd ~

# Kirok をダウンロード
git clone https://github.com/TadFuji/kirok-mcp.git
cd kirok-mcp

# 必要なパッケージをインストール
uv sync
```

</details>

<details>
<summary><b>🪟 Windows の場合</b></summary>

```powershell
# デスクトップに移動
cd $env:USERPROFILE\Desktop

# Kirok をダウンロード
git clone https://github.com/TadFuji/kirok-mcp.git
cd kirok-mcp

# 必要なパッケージをインストール
uv sync
```

> **Git が入っていない場合は？**
> [git-scm.com](https://git-scm.com/download/win) からインストールしてください。
> もしくは [GitHub のページ](https://github.com/TadFuji/kirok-mcp) から緑の「Code」ボタン → 「Download ZIP」でダウンロードして解凍してもOKです。

</details>

### ステップ 5：API キーの設定

<details>
<summary><b>🍎 Mac の場合</b></summary>

```bash
cp .env.example .env
```

`.env` ファイルをテキストエディタで開き、`your-api-key-here` の部分をステップ 3 でコピーした API キーに置き換えます：

```
GEMINI_API_KEY=AIzaSy...ここにキーを貼り付け...
```

</details>

<details>
<summary><b>🪟 Windows の場合</b></summary>

```powershell
Copy-Item .env.example .env
```

`.env` ファイルをメモ帳（または任意のテキストエディタ）で開き、`your-api-key-here` の部分をステップ 3 でコピーした API キーに置き換えます：

```
GEMINI_API_KEY=AIzaSy...ここにキーを貼り付け...
```

</details>

### ステップ 6：Claude Desktop に接続

Kirok をAIクライアントに接続します。ここでは最もよく使われる **Claude Desktop** での設定方法を説明します。

#### 設定ファイルの場所

| OS | ファイルの場所 |
|----|---------------|
| 🍎 Mac | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| 🪟 Windows | `%APPDATA%\Claude\claude_desktop_config.json` |

> **💡 開き方のコツ**：
> Claude Desktop のアプリ内で **設定**（歯車アイコン）→ **Developer**（開発者）→ **Edit Config**（設定を編集）から開けます。
> このメニューが見つからない場合は、上記のパスにあるファイルを直接テキストエディタで開いてください。

#### 設定内容を追加

設定ファイルを開いて、以下のように Kirok サーバーを追加します。**`/path/to/kirok-mcp` の部分は、ステップ 4 で Kirok をインストールした実際のフォルダパスに変更してください。**

<details>
<summary><b>🍎 Mac の設定例</b></summary>

```json
{
  "mcpServers": {
    "kirok": {
      "command": "uv",
      "args": [
        "run",
        "--directory", "/Users/あなたのユーザー名/kirok-mcp",
        "kirok-mcp"
      ]
    }
  }
}
```

> `/Users/あなたのユーザー名/kirok-mcp` を実際のパスに置き換えてください。
> パスが分からない場合は、kirok-mcp フォルダの中で `pwd` コマンドを実行すると確認できます。

</details>

<details>
<summary><b>🪟 Windows の設定例</b></summary>

```json
{
  "mcpServers": {
    "kirok": {
      "command": "uv",
      "args": [
        "run",
        "--directory", "C:\\Users\\あなたのユーザー名\\Desktop\\kirok-mcp",
        "kirok-mcp"
      ]
    }
  }
}
```

> `C:\\Users\\あなたのユーザー名\\Desktop\\kirok-mcp` を実際のパスに置き換えてください。
> **注意**：JSON ファイルではバックスラッシュ（`\`）を2つ続けて `\\` と書く必要があります。

</details>

<details>
<summary><b>📌 すでに他の MCP サーバーを使っている場合</b></summary>

設定ファイルにすでに他のサーバーがある場合は、`mcpServers` の中に `kirok` のエントリーを追加するだけでOKです：

```json
{
  "mcpServers": {
    "既存のサーバー": {
      "...": "..."
    },
    "kirok": {
      "command": "uv",
      "args": [
        "run",
        "--directory", "/path/to/kirok-mcp",
        "kirok-mcp"
      ]
    }
  }
}
```

</details>

#### Claude Desktop を再起動

設定ファイルを保存したら、**Claude Desktop を完全に終了**（ウィンドウを閉じるだけでなく、アプリ自体を終了）してから、**もう一度起動**してください。

### ステップ 7：動作確認

Claude Desktop で新しい会話を開き、以下を試してみてください：

> 「Kirok を使って、私の好きなプログラミング言語は Python だと覚えておいて」

Claude が `KIROK_retain` ツールを使って記憶を保存するはずです。

次に、**別の新しい会話**を開いて聞いてみてください：

> 「私の好きなプログラミング言語は？」

Claude が `KIROK_recall` を使って「Python」と答えてくれたら、セットアップ成功です！ 🎉

---

## 📖 ツール一覧

Kirok は **19 の MCP ツール**を提供します。5つのカテゴリに分かれています：

### 基本操作

| ツール | 説明 |
|--------|------|
| `KIROK_retain` | 記憶を保存（自動でキーワード抽出・重複チェック・意味解析を実行） |
| `KIROK_recall` | 記憶を検索（意味検索とキーワード検索のハイブリッド） |
| `KIROK_reflect` | 蓄積された記憶を分析して洞察を生成、メンタルモデルとして保存 |
| `KIROK_smart_retain` | 内容の重要度を10段階評価し、基準以上のみ保存（大量データ向け） |
| `KIROK_consolidate` | パターン検出を手動実行 |

### メモリ管理

| ツール | 説明 |
|--------|------|
| `KIROK_get_memory` | 特定の記憶の詳細を取得 |
| `KIROK_update_memory` | 既存の記憶の内容を更新 |
| `KIROK_forget` | 特定の記憶を削除（元に戻せません） |
| `KIROK_list_memories` | 記憶の一覧を表示（ページ分割対応） |

### メンタルモデル

| ツール | 説明 |
|--------|------|
| `KIROK_list_mental_models` | Reflect で生成された洞察の一覧 |
| `KIROK_get_mental_model` | メンタルモデルの詳細を取得 |
| `KIROK_delete_mental_model` | メンタルモデルを削除（元に戻せません） |
| `KIROK_refresh_mental_model` | 最新の記憶でメンタルモデルを更新 |

### バンク管理

| ツール | 説明 |
|--------|------|
| `KIROK_list_banks` | 全メモリバンクの一覧と記憶数を表示 |
| `KIROK_stats` | バンクの詳細な統計情報を取得 |
| `KIROK_clear_bank` | バンク内の全記憶を削除 |
| `KIROK_delete_bank` | バンクとその全データを完全削除 |

### 設定

| ツール | 説明 |
|--------|------|
| `KIROK_set_bank_config` | バンクの記憶/パターン検出の方針を設定 |
| `KIROK_get_bank_config` | バンクの現在の設定を確認 |

## ⚙️ 設定項目

すべての設定は `.env` ファイルで行います：

| 項目 | 必須 | 初期値 | 説明 |
|------|------|--------|------|
| `GEMINI_API_KEY` | ✅ | — | Google Gemini API キー（[無料で取得](https://aistudio.google.com/apikey)） |
| `KIROK_DB_PATH` | ❌ | `~/.kirok/memory.db` | データベースの保存場所 |
| `KIROK_DEDUP_THRESHOLD` | ❌ | `0.85` | 重複判定のしきい値（0.0〜1.0） |
| `KIROK_REFLECT_TIMEOUT` | ❌ | `300` | Reflect 操作のタイムアウト（秒） |
| `KIROK_CONSOLIDATION_TIMEOUT` | ❌ | `120` | パターン検出のタイムアウト（秒） |

## 🧪 仕組み

### 記憶 → 想起 → 考察 のサイクル

1. **記憶（Retain）**：情報を保存すると Kirok は以下を自動実行します
   - Gemini AI で意味ベクトル（embedding）を生成
   - 人名・ツール名・概念などの重要語を自動抽出
   - 既存の記憶と類似度を比較（0.85 以上で重複判定）
   - 重複がある場合は、追加 / 既存を更新 / スキップ を自動判断
   - SQLite と全文検索インデックス（FTS5）の両方に登録

2. **想起（Recall）**：検索すると Kirok は以下を実行します
   - 意味検索（コサイン類似度）
   - キーワード検索（FTS5 + BM25 ランキング）
   - 両方の結果を [Reciprocal Rank Fusion](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf) で統合
   - 検出されたパターンを優先表示、続いて個別の記憶を表示

3. **考察（Reflect）**：考察を実行すると Kirok は以下を実行します
   - 関連する記憶をまとめて取得
   - 既存のメンタルモデルと合わせて LLM に分析を依頼
   - 結果を新しいメンタルモデルとして保存

### メモリバンクとは？

記憶は **メモリバンク** に分類して保存します。フォルダのようなものと考えてください。例えば：

- `"work"` — 仕事関連の決定事項や学び
- `"personal"` — 個人的な好みやこだわり
- `"projects"` — プロジェクト固有の知識

バンクはいくつでも作成できます。AIエージェントが使っていくうちに、適切なバンク名を提案してくれます。

---

## ❓ よくある問題と解決方法

<details>
<summary><b>「uv: command not found」または「'uv' は認識されていません」と表示される</b></summary>

**uv がインストールされていないか、パスが通っていません。**

- [ステップ 2](#ステップ-2uv-のインストール) の uv インストールコマンドをもう一度実行してください
- インストール後、ターミナル / PowerShell を**閉じて開き直して**ください
- Mac の場合、`source ~/.zshrc` でシェル設定を再読み込みしてみてください

</details>

<details>
<summary><b>「Python 3.12+ が必要です」またはバージョンが古いと表示される</b></summary>

Python のバージョンを確認してください：
```bash
python3 --version   # Mac
python --version    # Windows
```

古いバージョンが表示される場合は、[ステップ 1](#ステップ-1python-のインストール) から Python 3.12 以上をインストールしてください。

</details>

<details>
<summary><b>Claude Desktop に Kirok が表示されない</b></summary>

1. Claude Desktop を**完全に終了**（ウィンドウを閉じるだけでなく、アプリ自体を終了）してから再起動してください
2. 設定ファイル（`claude_desktop_config.json`）のパスが正しいか確認してください
   - Mac: `/Users/ユーザー名/kirok-mcp`（スラッシュ区切り）
   - Windows: `C:\\Users\\ユーザー名\\Desktop\\kirok-mcp`（バックスラッシュ2つ）
3. JSON の文法ミス（カンマやカッコの抜け）がないか確認してください
4. Claude Desktop のログにエラーメッセージが出ていないか確認してください

</details>

<details>
<summary><b>「GEMINI_API_KEY が設定されていません」と表示される</b></summary>

1. `.env.example` ではなく `.env` にキーが書かれているか確認してください
2. `.env` ファイルを開いて `GEMINI_API_KEY=AIzaSy...` が正しく記載されているか確認してください
3. `=` の前後にスペースが入っていないか確認してください
4. APIキーが有効か [Google AI Studio](https://aistudio.google.com/) で確認してください

</details>

<details>
<summary><b>「git: command not found」または「'git' は認識されていません」と表示される</b></summary>

Git がインストールされていません：
- **Mac**: ターミナルで `xcode-select --install` を実行してください
- **Windows**: [git-scm.com](https://git-scm.com/download/win) からダウンロードしてください

Git を使わない方法もあります：[GitHub のページ](https://github.com/TadFuji/kirok-mcp) にアクセスし、緑色の「Code」ボタン → 「Download ZIP」からダウンロードして解凍してください。

</details>

---

## 📜 ライセンス

MIT ライセンス — 詳しくは [LICENSE](../LICENSE) をご覧ください。

## 🙏 謝辞

- [Model Context Protocol (MCP)](https://modelcontextprotocol.io) by Anthropic
- [Google Gemini API](https://ai.google.dev/) — 意味検索とLLMに使用
- [Mem0](https://github.com/mem0ai/mem0) — スマート重複排除のインスピレーション
- [Reciprocal Rank Fusion](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf) (Cormack et al., 2009)
