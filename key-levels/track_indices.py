#!/usr/bin/env python3
"""Track support and resistance on the Hong Kong and US cash indices in one run."""
import argparse
import datetime as dt
import json
import math
import os
import sys
import tempfile
from pathlib import Path

from index_tracker import INDICES, track_index
from index_tracker_view import dashboard, public_tracker, text_lines
from index_week import weekly_dashboard, weekly_profile, weekly_text
from key_levels import VERSION, load_market_data, normalize_symbol


def selected(values):
    groups = {"四大指數": list(INDICES), "ALL": list(INDICES),
              "美國三大指數": list(INDICES)[1:], "美股三大指數": list(INDICES)[1:]}
    result = []
    for value in values or ["四大指數"]:
        codes = groups.get(value.strip().upper()) or [normalize_symbol(value)]
        for symbol in codes:
            if symbol not in INDICES:
                raise ValueError("只支援恒指、道指、標普 500 及納指綜合。")
            if symbol not in result:
                result.append(symbol)
    return result


def input_files(items, symbols):
    found = {}
    for item in items:
        code, sep, path = item.partition("=")
        if not sep:
            if len(symbols) != 1:
                raise ValueError("多個指數的快照請用「代號=檔案路徑」。")
            code, path = symbols[0], item
        code = normalize_symbol(code)
        if code not in symbols or not path or code in found:
            raise ValueError("快照代號重複、未選用或路徑為空。")
        found[code] = Path(path)
    return found


def save(path, text, overwrite):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as f:
        pending = Path(f.name)
        try:
            f.write(text)
            f.flush()
        except Exception:
            pending.unlink(missing_ok=True)
            raise
    try:
        if overwrite:
            os.replace(pending, path)
        else:
            os.link(pending, path)
    finally:
        pending.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="追蹤恒指及美國三大指數的支持位、阻力位、回試及穿位情況。")
    parser.add_argument("symbols", nargs="*", help="預設四個現貨指數，亦可指定恒指、美國三大指數或單一代號")
    parser.add_argument("--weekly", action="store_true", help="以已收市天線編製本週關鍵位報告")
    parser.add_argument("--week-of", type=dt.date.fromisoformat, metavar="YYYY-MM-DD",
                        help="編製指定日期所屬週的關鍵位報告，以該市場日期為準")
    parser.add_argument("--input-json", action="append", default=[],
                        metavar="SYMBOL=PATH", help="天線或包含五分鐘行情的快照，可重複指定")
    parser.add_argument("--intraday-json", action="append", default=[],
                        metavar="SYMBOL=PATH", help="配對的五分鐘快照，可重複指定")
    parser.add_argument("--history-days", type=int, default=30, help="保留最近幾個交易日已結束追蹤的價位（預設 30）")
    parser.add_argument("--html", type=Path, help="追蹤表路徑，預設使用帶時間的檔名")
    parser.add_argument("--no-html", action="store_true", help="供純文字或 JSON 批次處理")
    parser.add_argument("--json", nargs="?", const="-", metavar="PATH", help="完整追蹤紀錄；省略路徑則輸出到 stdout")
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--no-extend", action="store_true", help="只取一年的天線")
    parser.add_argument("--overwrite", action="store_true", help="允許替換指定的輸出檔案")
    args = parser.parse_args(argv)
    weekly = args.weekly or args.week_of is not None
    try:
        symbols = selected(args.symbols)
        days = args.history_days
        if not 1 <= days <= 252 or not math.isfinite(args.timeout) or args.timeout <= 0:
            raise ValueError("觀察期須為 1–252 個交易日，連線逾時須為正數。")
        if args.html and args.no_html:
            raise ValueError("請指定 HTML 路徑或選擇略過 HTML。")
        daily = input_files(args.input_json, symbols)
        intra = input_files(args.intraday_json, symbols)
        hpath = None if args.no_html else args.html or Path(
            "Index_Key_Levels_"+dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")+".html")
        jpath = Path(args.json) if args.json and args.json != "-" else None
        paths = [p.resolve() for p in (hpath, jpath) if p]
        protected = {p.resolve() for p in list(daily.values())+list(intra.values())}
        protected.update(p.resolve() for p in Path(__file__).parent.glob("*.py"))
        protected.add(Path(__file__).with_name("SKILL.md").resolve())
        protected.update(p.resolve() for p in Path(__file__).with_name("assets").rglob("*") if p.is_file())
        if len(set(paths)) != len(paths) or protected.intersection(paths):
            raise ValueError("輸出檔不可互相重疊或覆寫輸入檔與程式。")
        if not args.overwrite and any(p.exists() for p in paths):
            raise ValueError("輸出檔已存在，請選其他檔名或確認覆寫。")
        reports = []
        for code in symbols:
            try:
                if daily and code not in daily:
                    raise ValueError("未提供這個指數的天線快照。")
                options = argparse.Namespace(input_json=daily.get(code), intraday_json=intra.get(code),
                                             no_extend=args.no_extend, timeout=args.timeout, save_raw=None, reaction_days=days)
                if weekly:
                    data, raw, _, _ = load_market_data(code, options, tracking=True, with_intraday=False)
                    snapshot = raw.get("keyLevelsIndexQuote") if code in daily else None
                    profile = weekly_profile(data, code, args.week_of, snapshot)
                else:
                    data, _, _, _ = load_market_data(code, options, tracking=True)
                    profile = public_tracker(track_index(data, code, days))
                profile["offline"] = code in daily
                reports.append(profile)
            except (ValueError, OSError, KeyError, TypeError, IndexError) as error:
                reports.append(dict(symbol=code, name=INDICES[code], error=str(error)))
        failed = [r for r in reports if r.get("error")]
        output = dict(version=VERSION, indices=reports)
        encoded = json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False) if args.json else None
        if hpath:
            save(hpath, weekly_dashboard(reports) if weekly else dashboard(reports), args.overwrite)
        if jpath:
            save(jpath, encoded, args.overwrite)
        if args.json == "-":
            print(encoded)
        else:
            for r in reports:
                if r.get("error"):
                    print(r["name"]+"　"+r["error"], file=sys.stderr)
                else:
                    if r["synthetic"]:
                        print("合成測試行情。")
                    elif r["offline"]:
                        print("已保存行情，離線重播。")
                    print("\n".join(weekly_text(r) if weekly else text_lines(r)))
            if hpath:
                print("\nHTML "+str(hpath.resolve()))
        return 0 if not failed else 1 if len(failed) < len(reports) else 2
    except (ValueError, OSError, TypeError) as error:
        print("key-levels: "+str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
