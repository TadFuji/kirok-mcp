#!/usr/bin/env python3
"""Amount Validator for Japanese Ceremonial Gifts.

Validates condolence money (香典) and wedding gifts (ご祝儀) amounts
for cultural taboos and expected ranges.

Usage:
    python amount_validator.py --amount 40000 --type funeral
    python amount_validator.py --amount 5000 --type funeral --relationship colleague --target parent
    python amount_validator.py --amount 30000 --type wedding --relationship friend

Output: JSON with validation results.
"""
import argparse
import json
import sys

# Expected ranges: (min, max) in yen
FUNERAL_RANGES = {
    ("colleague", "self"):       (5000, 10000),
    ("colleague", "parent"):     (3000, 10000),
    ("colleague", "spouse"):     (3000, 10000),
    ("colleague", "grandparent"): (3000, 5000),
    ("colleague", "child"):      (3000, 10000),
    ("friend", "self"):          (5000, 10000),
    ("friend", "parent"):        (3000, 10000),
    ("friend", "grandparent"):   (3000, 5000),
    ("relative", "parent"):      (50000, 100000),
    ("relative", "grandparent"): (10000, 50000),
    ("relative", "sibling"):     (30000, 50000),
    ("relative", "uncle_aunt"):  (10000, 30000),
    ("relative", "cousin"):      (5000, 10000),
    ("client", "self"):          (5000, 30000),
    ("client", "parent"):        (5000, 10000),
}

WEDDING_RANGES = {
    ("friend", "self"):          (30000, 30000),
    ("colleague", "self"):       (30000, 30000),
    ("boss", "self"):            (30000, 50000),
    ("subordinate", "self"):     (30000, 50000),
    ("relative", "sibling"):     (50000, 100000),
    ("relative", "cousin"):      (30000, 50000),
    ("relative", "nephew_niece"): (30000, 50000),
    ("client", "self"):          (30000, 30000),
}

GIFT_RANGES = {
    # お中元 (Summer gift)
    ("ochugen", "boss"):          (3000, 5000),
    ("ochugen", "colleague"):     (3000, 5000),
    ("ochugen", "client"):        (5000, 10000),
    ("ochugen", "relative"):      (3000, 5000),
    ("ochugen", "matchmaker"):    (5000, 10000),
    # お歳暮 (Year-end gift)
    ("oseibo", "boss"):           (3000, 5000),
    ("oseibo", "colleague"):      (3000, 5000),
    ("oseibo", "client"):         (5000, 10000),
    ("oseibo", "relative"):       (3000, 5000),
    ("oseibo", "matchmaker"):     (5000, 10000),
    # 入学祝い (School entry gift)
    ("school_entry", "grandchild"):   (10000, 50000),
    ("school_entry", "nephew_niece"): (5000, 30000),
    ("school_entry", "friend_child"): (3000, 10000),
    # 七五三 (Shichi-go-san)
    ("shichigosan", "grandchild"):    (10000, 30000),
    ("shichigosan", "nephew_niece"):  (5000, 10000),
    ("shichigosan", "friend_child"):  (3000, 5000),
    # 還暦・長寿祝い (Milestone birthday)
    ("kanreki", "parent"):        (10000, 50000),
    ("kanreki", "boss"):          (5000, 10000),
    ("kanreki", "colleague"):     (3000, 10000),
    ("kanreki", "grandparent"):   (10000, 30000),
    # 快気祝い (Recovery return gift — typically half to 1/3 of received amount)
    ("kaiki", "return"):          (1500, 5000),
}


def validate_amount(amount: int, event_type: str, relationship: str = "", target: str = "") -> dict:
    """Validate a ceremonial gift amount."""
    warnings = []
    errors = []

    # Check for even numbers (偶数) — based on 万円 (10,000-yen) units
    # In Japanese culture, the taboo is about the number of 万円 bills:
    # 1万=OK(奇数), 2万=NG(偶数), 3万=OK(奇数), 4万=NG(偶数+死), 5万=OK(奇数)
    # For amounts under 10,000, check 千円 units instead
    if amount >= 10000:
        man_units = amount // 10000
        is_even = man_units % 2 == 0
    else:
        man_units = amount // 1000
        is_even = man_units % 2 == 0

    if is_even and amount >= 10000:
        # Special case: 20000 is sometimes acceptable for weddings
        if event_type == "wedding" and amount == 20000:
            warnings.append({
                "code": "EVEN_NUMBER_WEDDING_20K",
                "message": "20,000円は近年許容される場合もありますが、30,000円が無難です",
                "severity": "warning",
            })
        else:
            errors.append({
                "code": "EVEN_NUMBER",
                "message": f"{amount:,}円は偶数（{man_units}万円）です。割り切れる＝縁が切れるを連想させます",
                "severity": "error",
            })

    # Check for 4 (死 = death)
    if "4" in str(amount):
        errors.append({
            "code": "CONTAINS_FOUR",
            "message": f"{amount:,}円は「4」を含みます。「死」を連想させます",
            "severity": "error",
        })

    # Check for 9 (苦 = suffering)
    if "9" in str(amount):
        errors.append({
            "code": "CONTAINS_NINE",
            "message": f"{amount:,}円は「9」を含みます。「苦」を連想させます",
            "severity": "error",
        })

    # Check range if relationship info provided
    range_check = None
    if relationship and target:
        if event_type == "funeral":
            ranges = FUNERAL_RANGES
        elif event_type == "wedding":
            ranges = WEDDING_RANGES
        else:
            ranges = GIFT_RANGES
        key = (relationship, target)
        if key in ranges:
            min_val, max_val = ranges[key]
            range_check = {
                "expected_min": min_val,
                "expected_max": max_val,
                "in_range": min_val <= amount <= max_val,
            }
            if amount < min_val:
                warnings.append({
                    "code": "BELOW_RANGE",
                    "message": f"{amount:,}円は相場（{min_val:,}〜{max_val:,}円）より低いです。失礼に思われる可能性があります",
                    "severity": "warning",
                })
            elif amount > max_val:
                warnings.append({
                    "code": "ABOVE_RANGE",
                    "message": f"{amount:,}円は相場（{min_val:,}〜{max_val:,}円）より高いです。相手に気を遣わせる可能性があります",
                    "severity": "warning",
                })

    # Bill-type check
    bill_note = None
    if event_type == "funeral":
        bill_note = "弔事では新札は避けてください。新札しかない場合は折り目を付けてから入れます"
    elif event_type == "wedding":
        bill_note = "慶事では新札を用意してください。銀行窓口で両替できます"
    elif event_type == "gift":
        bill_note = "お祝い事では新札を用意してください。快気祝いのお返しも新札が望ましいです"

    passed = len(errors) == 0

    return {
        "amount": amount,
        "formatted": f"{amount:,}円",
        "event_type": event_type,
        "relationship": relationship or "unspecified",
        "target": target or "unspecified",
        "passed": passed,
        "errors": errors,
        "warnings": warnings,
        "range_check": range_check,
        "bill_note": bill_note,
    }


def main():
    parser = argparse.ArgumentParser(description="Amount Validator for Japanese Ceremonies")
    parser.add_argument("--amount", type=int, required=True, help="Amount in yen")
    parser.add_argument("--type", type=str, required=True, choices=["funeral", "wedding", "gift"],
                        help="Event type: funeral, wedding, or gift (seasonal/celebration)")
    parser.add_argument("--relationship", type=str, default="",
                        help="Relationship: friend, colleague, boss, subordinate, relative, client")
    parser.add_argument("--target", type=str, default="",
                        help="Target: self, parent, spouse, grandparent, child, sibling, cousin, uncle_aunt, nephew_niece")
    args = parser.parse_args()

    result = validate_amount(args.amount, args.type, args.relationship, args.target)
    print(json.dumps(result, ensure_ascii=False, indent=2))

    sys.exit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
