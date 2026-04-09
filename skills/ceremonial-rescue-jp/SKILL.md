---
name: ceremonial-rescue-jp
description: "Japanese ceremonial etiquette rescue (冠婚葬祭レスキュー). Comprehensive guidance for ALL Japanese ceremonies — gift amounts, etiquette, attire, spoken lines, documents, NG-word checking via static knowledge + web search hybrid. Trigger on: 葬儀, 通夜, 香典, 弔電, 焼香, 法事, 四十九日, 訃報, お悔やみ, 喪中, 家族葬, 香典辞退, 結婚式, ご祝儀, 招待状, スピーチ, 祝電, 披露宴, 出産祝い, お中元, お歳暮, 暑中見舞い, 年賀状, 喪中はがき, 快気祝い, 内祝い, お返し, 入学祝い, 入園祝い, 卒業祝い, 七五三, お宮参り, 還暦, 古希, 喜寿, 米寿, 長寿祝い, いくら包む, 相場, のし, 熨斗, 冠婚葬祭, マナー, 礼儀作法, or any Japanese ceremony/etiquette question. Covers funerals, weddings, seasonal gifts, milestone celebrations, company-level responses, and belated condolences."
---

# 冠婚葬祭レスキュー (Ceremonial Rescue JP)

## Purpose

Resolve the anxiety of "what should I do?" for Japanese ceremonial occasions in a single interaction. This is a rescue system that delivers confidence through a comprehensive "response set" (対応セット).

**Core design principle:** Act autonomously. When a problem occurs (search fails, info is insufficient, tools error), solve it autonomously and always deliver a final result. Never tell the user "I couldn't find the information." Always reach a conclusion.

## Main Workflow

Every request follows these 9 steps in order:

1. **Profile Discovery** — Autonomously search workspace for user info (§2)
2. **Event Classification** — Determine funeral/wedding/other and sub-type (§3)
3. **Position Assessment** — 4-axis evaluation of user's social position (§4)
4. **Timing Assessment** — When the user learned about the event
5. **Company Mode Check** — Personal vs. department vs. company response (§5)
6. **📚 Static Knowledge Load** — Load invariant etiquette, NG words, ceremony procedures from `references/` (§6)
7. **🔍 Web Research** — Search for latest amounts, trends, year-specific info only (§6)
8. **Response Set Generation** — Build 7-layer response, generate text + files with template + profile merge (§7, §8)
9. **Cross-Check** — NG words, amounts, dates, consistency verification (§9)

---

## §2 User Profile Discovery

At skill activation, autonomously search the workspace for user information. Never hardcode specific filenames.

### Search Steps

1. **List workspace root + global config** — Get directory listing of workspace root AND platform config directories (e.g., `~/.gemini/`, `~/.config/`). Profile info often lives outside the workspace.
2. **Identify candidates** by signals (priority order):
   - a. Platform-defined profile files: `USER.md`, `CLAUDE.md`, `IDENTITY.md`, `SOUL.md`, `GEMINI.md`, `README.md`, `AGENTS.md`, `user_profile.md`, `profile.md`
   - b. Keyword matches: `about`, `profile`, `me`, `user`, `自己紹介`, `プロファイル`
   - c. Context folders: `context/`, `config/`, `docs/`, `notes/`
   - d. Other `.md`/`.txt` files (if ≤10 files, scan first lines)
3. **Read candidates** — Extract: name, age/birth year, employer, title, region, family, religion
4. **Store internally** — Hold in context. Do not report to user.
5. **Missing = OK** — Continue without. Never add extra questions.

### Profile Rules

- Found → never re-ask. Auto-insert into documents.
- Not found → don't ask unless essential.
- User input overrides profile data.
- Never quote profile data in chat — only in documents.

---

## §3 Event Classification

### Funeral Events (弔事)

| Scene | Trigger | Key Difference |
|---|---|---|
| Attending funeral | Ceremony invitation | Full response set |
| Family funeral (家族葬) | 「家族葬で行います」 | No attendance; telegram/flowers/letter |
| Condolence declined | 「香典は辞退」 | Alternative condolence methods |
| Learned later | Found out after ceremony | Belated visit/letter/mailing |
| Company response | As manager/HR | Internal notice + company arrangements |

### Wedding Events (慶事)

| Scene | Trigger | Key Difference |
|---|---|---|
| Attending wedding | Invitation received | Full: gift, attire, RSVP, etiquette |
| Declining wedding | Cannot attend | Decline RSVP + gift decision |
| No-ceremony marriage | 式なし報告 | Gift/message/dinner options |
| Flat-fee wedding | 「会費制です」 | No separate gift |
| Speech requested | スピーチ依頼 | Full speech draft |

### Seasonal Events (季節行事)

| Scene | Trigger | Key Difference |
|---|---|---|
| お中元 | 夏の贈り物 | 時期: 7月上旬〜8月中旬（地域差あり）。品物選び+送り状 |
| お歳暮 | 年末の贈り物 | 時期: 12月上旬〜20日。品物選び+送り状 |
| 年賀状 | 年始の挨拶 | 文面、喪中確認（→喪中はがき or 寒中見舞い）、投函時期 |

### Celebration Events (お祝い)

| Scene | Trigger | Key Difference |
|---|---|---|
| 快気祝い | 病気回復のお返し | 本人→周囲。お見舞いの半返し〜1/3。消え物が定番 |
| 入学祝い | 入学・入園 | 金額は学校レベル×関係性で変動。お返し=内祝い |
| 七五三 | 3/5/7歳のお祝い | 親族間が一般的。親は準備側（神社+食事会）。伝統: 3歳=男女, 5歳=男, 7歳=女。ただし近年は性別問わず祝う家庭も多い→ご家族に確認の上、祝う方向で案内 |
| 還暦祝い | 60歳の祝い | 赤いもの贈呈の慣習。古希(70)/喜寿(77)/米寿(88)も同フロー |

**Cannot classify?** → Ask 1 multiple-choice question.

---

## §4 Position Assessment Engine (4 Axes)

Infer all 4 axes from user input. State inferences as 「〇〇と解釈しました」. Ask at most 1 question (multiple-choice) only if amount-critical axes are unclear.

| Axis | Content |
|---|---|
| **1. User's Role** (誰として) | Friend, Colleague, Client, Relative, Neighbor, Parent peer |
| **2. Relationship Target** (誰に何が) | The person, Spouse, Parent, Grandparent, Child, Sibling |
| **3. Closeness** (距離感) | Close (親しい) → higher amounts / Normal (default) / Formal (形式的) → lower amounts |
| **4. Function** (役割) | Attendee, Absentee, Representative, Organizer, Visit-only |

**Seasonal/Celebration events**: Axes 1+3 are sufficient (Role + Closeness). Axis 2 is usually the person themselves. Axis 4 defaults to Gift-giver.

---

## §5 Company Mode

Applies when any of these:
- Work relationship (colleague, boss, client)
- Profile shows managerial title
- User mentions company-level response

Load `references/company_mode.md` for the 3-layer decision framework, internal notice templates, HR checklist, and flower/telegram arrangements.

---

## §6 Hybrid Knowledge Strategy

**This is the core of the skill.** Invariant cultural knowledge is loaded from `references/` and only volatile information (amounts, trends) is searched on the web.

### Step 1: Load Static Knowledge

Based on the classified event, load the relevant references:

| File | Contents | When to Load |
|---|---|---|
| `references/etiquette.md` | Envelope writing, ceremony procedures, spoken lines, attire, invitation replies, behavioral NG, speech structure, regional basics | Always |
| `references/ng_words.md` | Taboo words, sect-specific NG, alternatives | Always |
| `references/company_mode.md` | 3-layer decision, internal notices, HR checklist, flower/telegram | When Company Mode applies |

These files contain **invariant knowledge** — linguistic taboos, religious rules, ceremony procedures, and etiquette that do not change over time.

### Step 2: Web Search (Volatile Information Only)

Search the web **only** for information that changes with time, prices, or social trends:

| Information Need | Search Query Pattern | Why Web? |
|---|---|---|
| **Gift amount (香典/ご祝儀)** | `{香典/ご祝儀} 相場 {関係性} {年}` | Price inflation, social changes |
| **Seasonal gift amount** | `{お中元/お歳暮} 相場 {関係性} {年}` | Price changes |
| **Celebration gift amount** | `{入学祝い/七五三/還暦} 相場 {関係性} {年}` | Same |
| **Recovery gift amount** | `快気祝い お返し 相場 {年}` | Same |
| **Trending gift items** | `{お中元/お歳暮} 人気 おすすめ {年}` | Trends change yearly |
| **Current year info** | `年賀状 {年} 干支` | Changes every year |
| **Regional latest customs** | `{地域名} {イベント} 慣習 最新` | Regional customs evolve |
| **Latest etiquette trends** | `{結婚式/葬儀} マナー 最新 {年}` | Social norms evolve |
| **Telegram services** | `{弔電/祝電} サービス おすすめ {年}` | Services update |

### Search Execution Rules

1. **Run 1–3 searches** per request. Static knowledge covers most needs, so fewer searches are required.
2. **Prioritize reputable sources**: マイナビ, All About, ゼクシィ, 小さなお葬式, イオンのお葬式
3. **Cross-reference**: If amounts differ across sources, present the range and mark ★推奨 on safest
4. **Include the year** when presenting web-sourced amounts
5. **Adapt to context**: If user provides region info, add region-specific search

### Autonomous Problem Resolution

```
IF web search fails or returns insufficient results:
  Step 1: Rephrase query (try 2 alternative phrasings)
  Step 2: Broaden scope (remove specificity constraints)
  Step 3: Fall back to amount_validator.py ranges as baseline
  Step 4: If still insufficient → provide guidance from references/ +
          amount_validator.py fallback ranges + clearly note:
          「最新の相場は○○（式場/葬儀社）にご確認ください」

NEVER say: "情報が見つかりませんでした" or "回答できません"
ALWAYS deliver a complete response set — references/ ensure baseline quality
```

### Research Quality Checks

- **Freshness**: Prefer articles from the last 2 years. Flag anything older than 5 years.
- **Consensus**: When sources disagree, present the range and recommend the safest choice
- **Regional awareness**: If profile shows region ≠ default (Kanto), search for regional differences

---

## §7 Response Set — 7 Layers

Auto-select layers based on the situation. Never ask "what do you need?" — determine automatically.

### Layer 1: 🔍 Things to Confirm First
Items the user should verify (sect, ceremony format, date, venue).

### Layer 2: 📋 Action Timeline
Time-ordered checklist with urgency indicators:
- 🔴 **今すぐ** (within hours)
- 🟡 **今日中** (today)
- 🟢 **数日以内** (within days)

### Layer 3: 💰 Amount Guidance
- **Web-sourced** recommended amount with ★推奨 mark on safest choice
- Range with source citations
- Reasoning for the recommendation
- Run `scripts/amount_validator.py` to verify (even numbers, 4/9 check)

### Layer 4: 📝 Written Messages (multi-channel)
Generate per channel as needed. Use templates from `assets/templates/`.

| Channel | Format | When |
|---|---|---|
| Formal email | .docx | Work absence, formal condolence |
| LINE/Chat | In-chat text (≤300 chars) | Casual condolence/congratulation |
| Telegram (弔電/祝電) | .docx | When telegram needed |
| Speech | .docx | When speech requested |

### Layer 5: 🗣️ Spoken Lines
Use spoken line examples from `references/etiquette.md` (§ 口頭セリフ集). Provide in "say this exactly" format:
- Keep each line ≤ 30 characters (memorizable)
- Vary by closeness level (親しい / 普通 / 形式的)
- Include NG examples (what NOT to say)

### Layer 6: 👔 Attire & Belongings
Use `references/etiquette.md` (§ 服装持ち物) as baseline. Supplement with web search only for latest trend questions.

### Layer 7: ⚠️ NG Warnings
Refer to `references/ng_words.md` for knowledge, then run `scripts/ng_word_checker.py` on ALL generated text for programmatic verification.

### Layer Selection Matrix

| Situation | L1 | L2 | L3 | L4 | L5 | L6 | L7 |
|---|---|---|---|---|---|---|---|
| Attending funeral | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Family funeral | ✓ | ✓ | - | ✓ | - | - | ✓ |
| Condolence declined | - | ✓ | - | ✓ | ✓ | - | ✓ |
| Learned later | - | ✓ | ✓ | ✓ | ✓ | - | ✓ |
| Attending wedding | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Declining wedding | - | ✓ | ✓ | ✓ | - | - | ✓ |
| No-ceremony marriage | - | ✓ | ✓ | ✓ | - | - | ✓ |
| Speech requested | - | - | - | ✓ | ✓ | - | ✓ |
| "How much?" only | - | - | ✓ | - | - | - | - |
| Company response | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| お中元・お歳暮 | - | ✓ | ✓ | ✓ | - | - | ✓ |
| 年賀状 | - | ✓ | - | ✓ | - | - | ✓ |
| 快気祝い | - | ✓ | ✓ | ✓ | - | - | ✓ |
| 入学祝い | - | ✓ | ✓ | ✓ | - | - | ✓ |
| 七五三 | - | ✓ | ✓ | ✓ | - | - | ✓ |
| 還暦祝い | - | ✓ | ✓ | ✓ | - | - | ✓ |

---

## §8 File Generation

Use platform-available skills/tools for file creation:

| Output | Tool/Skill | Template |
|---|---|---|
| Email/letter .docx | docx skill | `assets/templates/condolence_email.md` etc. |
| Checklist .pdf | pdf skill | `assets/templates/checklist_template.md` |
| Seasonal gift letter | docx skill | `assets/templates/seasonal_gift.md` |
| New Year card | docx skill | `assets/templates/new_year_card.md` |
| Celebration message | docx skill | `assets/templates/celebration_message.md` |
| Summary .pdf | pdf skill | Compile all layers |

Templates use `{{variable}}` placeholders. Merge profile data + web research results automatically.

---

## §9 Cross-Check & Validation

Before delivering the response set:

1. **NG Word Scan**: Run `scripts/ng_word_checker.py --type <event>` on all generated text. Use `funeral`, `wedding`, or `celebration` type. Replace detections with alternatives.
2. **Amount Check**: Run `scripts/amount_validator.py` — verify no even numbers, no 4/9, within expected range. Use `--type gift` for seasonal/celebration events.
3. **Date/Rokuyō Check**: Run `scripts/date_validator.py` if dates are known — warn on 友引×funeral or 仏滅×wedding.
4. **Source Consistency**: Verify amounts cited from web are consistent across generated documents
5. **Keigo Uniformity**: Ensure politeness level is consistent across all channels
6. **Profile Merge Check**: Verify profile data was inserted into all signature/sender fields

### NG Word Rules

Full NG word dictionary with alternatives: `references/ng_words.md`
Programmatic checker: `scripts/ng_word_checker.py`

Key invariant rules (always apply, no web search needed):
- **Funeral**: 重ね言葉 NG, direct death words NG
- **Wedding**: Separation/remarriage words NG, no punctuation in messages
- **Celebration**: Illness recall NG, recurrence NG, 重ね言葉 NG
- **Sect-specific**: 浄土真宗→「ご冥福」NG, 神式→「御仏前」NG, キリスト教→「御霊前」NG

---

## §10 Example

**Input:** 「同僚の田中さんのお父様が亡くなったと聞きました。通夜に行こうと思います。」

**Agent behavior:**

1. Profile discovery → check workspace for user info
2. Classify → Funeral, attending
3. Position → Colleague × Parent × Normal closeness × Attendee
4. Web search → `香典 相場 同僚の親 2026`, `通夜 マナー 注意点`, `通夜 服装 持ち物`
5. Build response set (all 7 layers)
6. Cross-check with scripts

**Expected output structure:**

```
🔍 確認事項
  - 宗派 → 不明の場合は「御霊前」で対応
  - 通夜の日時・場所

📋 アクションタイムライン
  🔴 今すぐ: 通夜の日時・場所を確認
  🟡 今日中: 香典を用意、喪服の準備
  🟢 数日以内: 忌引の場合は欠勤連絡

💰 金額ガイド（Web検索結果 + amount_validator.py検証）
  同僚のお父様 → ★5,000円（相場: 3,000〜10,000円）
  出典: ○○（2026年記事）

📝 メッセージ
🗣️ 口頭セリフ
👔 持ち物チェックリスト
⚠️ NG注意
```

---

## §11 Constraints

- No legal advice (inheritance, wills → "consult a specialist")
- No religious value judgments (treat all sects equally)
- Amounts are "general market rates," never "the correct answer"
- Personal info only in documents, never in chat explanations
- Always cite sources when presenting web-searched amounts

---

## Gotchas

- ❌ Never use 「ご冥福をお祈りします」 for 浄土真宗. Use 「お悔やみ申し上げます」 instead. This is the #1 mistake.
- ❌ Never suggest even-numbered amounts or amounts containing 4/9. Always run `amount_validator.py`.
- ❌ Never include 重ね言葉 in funeral contexts (重ね重ね, たびたび, またまた). Always run `ng_word_checker.py`.
- ❌ Never suggest new bills for funerals or old bills for weddings. The symbolism is reversed.
- ❌ Never give up. If web search fails, rephrase and retry. If still failing, provide general guidance and recommend a confirmation source. The user came because they're anxious — leaving them without an answer is unacceptable.
- ❌ Never use illness-related words (病気, 入院, 倒れる, 再発) in 快気祝い messages. They remind of illness recurrence.
- ⚠️ Family funeral (家族葬) does NOT mean "small funeral you can attend." Default to not attending unless explicitly invited.
- ⚠️ Always verify web search results for freshness. Articles older than 5 years should be flagged and cross-referenced with newer sources.
- ⚠️ When sources disagree on amounts, present the range and clearly mark ★推奨 on the safest (most commonly cited) option.
- ⚠️ LINE/chat messages under 300 characters. A wall of text feels burdensome.
- ⚠️ Spoken lines in memorizable format (≤30 chars each). No explanatory text mixed in.
- ⚠️ Profile data should be silently used — never say "your profile says you work at X."
- ⚠️ 快気祝い is the SICK PERSON giving back—not 「お見舞い」 (which is given to the sick person). Confusing the direction is a common error.
- ⚠️ お中元/お歳暮 timing differs by region (especially 関東 vs 関西). Always search for regional timing when user's region is known.
- ⚠️ 七五三 5歳は伝統的に男の子の行事だが、近年は女の子も祝う家庭が増えている。「お祝い不要」と読める回答はNG — 結論（お祝いしましょう）を先に出し、伝統との違いは補足として添える。
