#!/usr/bin/env python3
"""NG Word Checker for Japanese Ceremonial Contexts.

Scans text for taboo words (忌み言葉) in funeral and wedding contexts,
checks sect-specific NG expressions, and suggests alternatives.

Usage:
    python ng_word_checker.py --text "ご冥福をお祈りいたします" --type funeral
    python ng_word_checker.py --text "text" --type funeral --sect 浄土真宗
    echo "text" | python ng_word_checker.py --type funeral
    python ng_word_checker.py --file message.txt --type wedding

Output: JSON with detected NG words and suggested alternatives.
"""
import argparse
import json
import sys

# --- NG Word Database ---

FUNERAL_NG = {
    # Repeated words (重ね言葉)
    "重ね重ね": "加えて / 深く",
    "たびたび": "よく",
    "またまた": "再度 / 改めて",
    "いよいよ": "ついに / とうとう",
    "ますます": "一層 / さらに",
    "しばしば": "よく",
    "くれぐれも": "どうぞ / 十分に",
    "わざわざ": "ご丁寧に",
    "次々": "続けて",
    "返す返す": "思い返すと",
    # Death-related direct expressions
    "死ぬ": "ご逝去 / 亡くなる",
    "死んだ": "亡くなった / ご逝去された",
    "死亡": "ご逝去",
    "急死": "急逝 / 突然のご逝去",
    "生きていた頃": "ご生前 / お元気だった頃",
    # Unlucky expressions
    "浮かばれない": "（使わない）",
    "消える": "（使わない）",
    "落ちる": "（使わない）",
    "苦しむ": "ご闘病 / お辛い時期",
}

WEDDING_NG = {
    # Separation/ending words
    "別れる": "（使わない）",
    "切れる": "（使わない）",
    "切る": "（使わない）",
    "離れる": "（使わない）",
    "壊れる": "（使わない）",
    "割れる": "（使わない）",
    "破れる": "（使わない）",
    "去る": "（使わない）",
    "飽きる": "（使わない）",
    "冷える": "（使わない）",
    "冷める": "（使わない）",
    "薄い": "（使わない）",
    "浅い": "（使わない）",
    "終わる": "お開きにする",
    "終わり": "お開き",
    # Remarriage implications
    "再び": "（使わない）",
    "繰り返す": "（使わない）",
    "戻る": "（使わない）",
    "重ねて": "加えて",
    # Repeated words (shared with funeral)
    "重ね重ね": "加えて",
    "たびたび": "よく",
    "またまた": "改めて",
    "いよいよ": "ついに",
    "ますます": "一層",
}

SECT_NG = {
    "浄土真宗": {
        "ご冥福": "お悔やみ申し上げます / 哀悼の意を表します",
        "冥福": "お悔やみ / 哀悼の意",
        "御霊前": "御仏前",
        "草葉の陰": "お浄土から",
        "天国": "お浄土 / 極楽浄土",
        "安らかにお眠りください": "安らかにお旅立ちください",
    },
    "神式": {
        "御仏前": "御玉串料 / 御榊料",
        "成仏": "（使わない — 仏教用語）",
        "供養": "追悼 / お祀り",
    },
    "キリスト教": {
        "御霊前": "御花料",
        "御仏前": "御花料",
        "成仏": "天に召される",
        "供養": "追悼",
    },
}

CELEBRATION_NG = {
    # Illness recall (快気祝い向け — 病気の再発を連想させる)
    "病気": "ご体調 / お加減",
    "入院": "ご療養",
    "倒れる": "（使わない）",
    "長引く": "（使わない）",
    "再発": "（使わない）",
    "弱る": "（使わない）",
    "衰える": "（使わない）",
    "寝込む": "（使わない）",
    # Recurrence/repetition (繰り返し — 不幸の再来を連想)
    "繰り返す": "（使わない）",
    "再び": "（使わない）",
    # Shared celebration NG (重ね言葉 — お祝い全般)
    "重ね重ね": "加えて",
    "たびたび": "よく",
    "またまた": "改めて",
    "いよいよ": "ついに",
    "ますます": "一層",
}


def check_text(text: str, event_type: str, sect: str = "") -> dict:
    """Check text for NG words and return findings."""
    findings = []

    # Select NG dictionary
    if event_type == "funeral":
        ng_dict = FUNERAL_NG
    elif event_type == "wedding":
        ng_dict = WEDDING_NG
    elif event_type == "celebration":
        ng_dict = CELEBRATION_NG
    else:
        ng_dict = {**FUNERAL_NG, **WEDDING_NG, **CELEBRATION_NG}

    # Check general NG words
    for ng_word, alternative in ng_dict.items():
        if ng_word in text:
            pos = text.find(ng_word)
            findings.append({
                "word": ng_word,
                "position": pos,
                "context": text[max(0, pos - 10):pos + len(ng_word) + 10],
                "category": "general",
                "alternative": alternative,
                "severity": "error",
            })

    # Check sect-specific NG words
    if sect:
        # Normalize sect name
        sect_key = sect
        for key in SECT_NG:
            if key in sect:
                sect_key = key
                break

        if sect_key in SECT_NG:
            for ng_word, alternative in SECT_NG[sect_key].items():
                if ng_word in text:
                    pos = text.find(ng_word)
                    findings.append({
                        "word": ng_word,
                        "position": pos,
                        "context": text[max(0, pos - 10):pos + len(ng_word) + 10],
                        "category": f"sect:{sect_key}",
                        "alternative": alternative,
                        "severity": "error",
                    })

    return {
        "input_text": text,
        "event_type": event_type,
        "sect": sect or "unspecified",
        "total_findings": len(findings),
        "passed": len(findings) == 0,
        "findings": findings,
    }


def main():
    parser = argparse.ArgumentParser(description="NG Word Checker for Japanese Ceremonies")
    parser.add_argument("--text", type=str, help="Text to check")
    parser.add_argument("--file", type=str, help="File to read text from")
    parser.add_argument("--type", type=str, required=True, choices=["funeral", "wedding", "celebration", "all"],
                        help="Event type: funeral, wedding, celebration, or all")
    parser.add_argument("--sect", type=str, default="", help="Religious sect (e.g., 浄土真宗, 神式, キリスト教)")
    args = parser.parse_args()

    if args.text:
        text = args.text
    elif args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            text = f.read()
    elif not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        parser.error("Provide --text, --file, or pipe text via stdin")
        return

    result = check_text(text, args.type, args.sect)
    print(json.dumps(result, ensure_ascii=False, indent=2))

    # Exit with error code if NG words found
    sys.exit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
