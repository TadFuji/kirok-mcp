# 欠勤連絡テンプレート (Absence Notice)

**用途:** 通夜・葬儀参列のための欠勤連絡メール
**出力形式:** .docx
**必須変数:** `{{boss_name}}`, `{{deceased_name}}`, `{{relationship}}`, `{{absence_dates}}`, `{{sender_name}}`
**敬語レベル:** フォーマル（社内）

---

## テンプレート本文

件名：忌引休暇のお願い

{{boss_name}}

お疲れ様です。{{sender_name}}です。

私事で恐縮ですが、{{relationship}}の{{deceased_name}}が
{{death_date}}に他界いたしました。

つきましては、通夜・告別式に参列するため、
{{absence_dates}}の間、忌引休暇をいただきたく存じます。

■ 通夜：{{wake_date}}（{{wake_time}}〜）
■ 告別式：{{funeral_date}}（{{funeral_time}}〜）
■ 場所：{{venue}}

不在中の業務について：
・{{handover_notes}}
・緊急の場合は携帯（{{phone}}）までご連絡ください

ご迷惑をおかけいたしますが、よろしくお願いいたします。

{{sender_name}}

---

## 注意事項

- 忌引日数は会社の規定を確認（一般的な目安は下記）
  - 配偶者：10日
  - 父母：7日
  - 子：5日
  - 祖父母：3日
  - 兄弟姉妹：3日
  - 配偶者の父母：3日
  - おじ・おば：1日
- 上記はあくまで目安。就業規則を必ず確認すること
