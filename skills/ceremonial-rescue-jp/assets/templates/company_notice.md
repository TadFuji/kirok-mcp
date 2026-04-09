# 社内通知テンプレート (Company Notice)

**用途:** 社内向けの訃報・慶事の通知
**出力形式:** テキスト（メール or チャット）
**必須変数:** `{{employee_name}}`, `{{department}}`, `{{event_type}}`
**敬語レベル:** フォーマル（社内）

---

## 弔事の社内通知（メール）

件名：【訃報】{{department}} {{employee_name}}さんの{{relationship}}ご逝去

```
各位

{{department}} {{employee_name}}さんの{{relationship}}（{{deceased_name}}様）が
{{death_date}}にご逝去されました

■ 通夜
  日時：{{wake_date}}（{{wake_day}}）{{wake_time}}〜
  場所：{{venue}}（{{venue_address}}）

■ 告別式
  日時：{{funeral_date}}（{{funeral_day}}）{{funeral_time}}〜
  場所：同上

■ 宗旨：{{sect}}

■ 喪主：{{mourner_name}}様（{{mourner_relationship}}）

■ 弔電送付先
  {{venue}} 気付 {{family_name}}家

■ 対応
  ・{{company_response}}
  ・{{employee_name}}さんは{{absence_period}}まで忌引休暇の予定です

ご不明な点は{{contact_dept}}{{contact_name}}（{{contact_ext}}）まで
お問い合わせください
```

## 弔事の社内通知（Slack/Teams）

```
📢 【訃報】{{department}} {{employee_name}}さんの{{relationship}}がご逝去されました

通夜: {{wake_date}} {{wake_time}}〜 @{{venue}}
告別式: {{funeral_date}} {{funeral_time}}〜 @同上
宗旨: {{sect}}

部署として供花・弔電を手配します。
個人でのご弔意は各自のご判断でお願いします。
{{employee_name}}さんは{{absence_period}}まで忌引休暇の予定です。

詳細・ご質問は{{contact_name}}まで。
```

## 慶事の社内通知（Slack/Teams）

```
🎉 {{department}} {{employee_name}}さんが{{event_date}}にご結婚されました！

部署としてお祝いをお渡しする予定です。
有志でメッセージカードを作成しますので、
一言メッセージがある方は{{deadline}}までに{{organizer_name}}まで。
```
