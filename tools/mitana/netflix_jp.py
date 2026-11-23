#!/usr/bin/env python3
"""Netflix 公式の日本の週間トップ10を「全部の週」TMDB の作品と結び付けて JSON にする（2026-11-19 本人「全て丁寧に入れましょう」）
※ 2026-11-23 から、元は SSD の Mitana/tools/netflix/make_netflix_jp_all.py。ここ（サイトのリポジトリ）の物を GitHub が毎日動かす：
   python3 -I tools/mitana/netflix_jp.py <all-weeks-countries.tsv> mitana/data/netflix-jp.json --if-new

使い方：TMDB_KEY=<鍵> python3 -I make_netflix_jp_all.py <all-weeks-countries.tsv> <出力.json> [--all]
- --all：94か国全部（2026-11-19 本人「とりあえず SSD に全て入れといて」）。weeks が {国: {週: {...}}} になる。アプリには入れず tools/netflix/data/ に置く
- 表は Netflix が公式に配っている物：https://www.netflix.com/tudum/top10/data/all-weeks-countries.tsv（毎週火曜に更新）
- 日本（JP）の 2021-07-04 の週から全部。作品の表（items）と、週ごとの順位（weeks：作品の番号を1位から順に）
- 合わせ方（丁寧に）：
  1. 英語の題名で TMDB を探す（映画は search/movie、シリーズは search/tv）
  2. 題名（英語・元の題名）がぴったり合う物だけを候補に。トップ10に初めて入った年より後に公開された物は外す
  3. 候補が複数なら、日本語の題名がある物 → 人気の順
  4. ぴったりが無ければ、単語の重なり（6割以上）で近い題名（Detective Conan the Movie: X → Detective Conan: X）。それでも無ければ合わない（一覧に出す）
     ※「:」の前だけで探すのは間違いが多かった（Brave: Gunjo Senki → メリダ）のでやめた
- match_cache.json：合わせた結果を残す（毎週の更新では新しい作品だけ探す）
- overrides.json：手で直す {"TV|Title": {"tmdb": 123, "media": "tv"}}、外すなら {"TV|Title": null}
- TMDB の鍵は環境変数 TMDB_KEY（ファイルに書かない）
"""
import csv, json, os, re, sys, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor

KEY = os.environ["TMDB_KEY"]
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "match_cache.json")
OVERRIDES = os.path.join(HERE, "overrides.json")


def tmdb(path, **q):
    q["api_key"] = KEY
    url = "https://api.themoviedb.org/3/%s?%s" % (path, urllib.parse.urlencode(q))
    for i in range(4):
        try:
            return json.load(urllib.request.urlopen(url, timeout=30))
        except Exception:
            time.sleep(1 + i * 2)
    return {}


def norm(s):
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


def has_japanese(s):
    return bool(re.search(r"[぀-ヿ一-鿿]", s or ""))


def year(s):
    return int(s[:4]) if s and len(s) >= 4 and s[:4].isdigit() else None


INDIAN = {"hi", "ta", "te", "ml", "kn", "mr", "bn"}


def search(title, media, first_year, indian=False):
    res = tmdb("search/" + media, query=title, language="ja-JP").get("results", [])
    key = norm(title)
    # 英語の題名は en-US で取らないと比べられないので、候補の英語の題名も取る
    en = {r["id"]: r for r in tmdb("search/" + media, query=title, language="en-US").get("results", [])}
    exact = []
    for r in res:
        e = en.get(r["id"], {})
        names = {norm(e.get("name") or e.get("title")), norm(r.get("original_name") or r.get("original_title")), norm(r.get("name") or r.get("title"))}
        if key in names:
            y = year(r.get("first_air_date") or r.get("release_date"))
            if y and first_year and y > first_year:
                continue          # トップ10に入った年より後の作品はありえない
            exact.append(r)
    if not exact:
        return None
    if indian:
        # インド映画の吹き替え（Beast (Tamil) など）：インドの言葉の作品 → トップ10に入った年に近い物（2026-11-20。同じ題名の洋画と合っていた）
        def key(r):
            y = year(r.get("first_air_date") or r.get("release_date")) or 0
            return (r.get("original_language") in INDIAN, -abs((first_year or y) - y))
        exact.sort(key=key, reverse=True)
        return exact[0]
    exact.sort(key=lambda r: (has_japanese(r.get("name") or r.get("title")), r.get("popularity", 0)), reverse=True)
    return exact[0]


STOP = {"the", "a", "an", "of", "and", "movie", "film", "season", "part", "jp"}


def words(s):
    s = re.sub(r"\(\d{4}\)", " ", (s or "").lower())
    return {w for w in re.findall(r"[a-z0-9]+", s) if w not in STOP}


def fuzzy(title, media, first_year):
    """題名が少し違う物（Detective Conan the Movie: X → Detective Conan: X）。単語の重なりで近い物"""
    q = words(title)
    if len(q) < 2:
        return None
    m = re.search(r"\((\d{4})\)", title)
    want_year = int(m.group(1)) if m else None
    query = re.sub(r"\(\d{4}\)|\bthe movie\b|[-–]+", " ", title, flags=re.I)
    query = re.sub(r"\s+", " ", query).strip()
    best = None
    for qq in {query, title}:
        for r in tmdb("search/" + media, query=qq, language="en-US").get("results", [])[:10]:
            y = year(r.get("first_air_date") or r.get("release_date"))
            if y and first_year and y > first_year:
                continue
            if want_year and y and abs(y - want_year) > 1:
                continue
            c = words(r.get("name") or r.get("title")) | set()
            o = words(r.get("original_name") or r.get("original_title"))
            sim = max(len(q & c) / len(q | c) if c else 0, len(q & o) / len(q | o) if o else 0)
            if sim >= 0.6 and (best is None or sim > best[0] or (sim == best[0] and r.get("popularity", 0) > best[1].get("popularity", 0))):
                best = (sim, r)
    return best[1] if best else None


def clean(title):
    """飾りを外す：(U.S.)・(Hindi)・(English)・(Extended Version)・: Extended Cut など（2026-11-20）"""
    t = re.sub(r"\s*\((U\.S\.|U\.K\.|Hindi|English|Tamil|Telugu|Malayalam|Kannada|Spanish|Dubbed|Subbed|Extended Version|Extended Cut|Director's Cut|Reloaded Version|Raw & Undekha|Uncut)\)", "", title, flags=re.I)
    t = re.sub(r"[:\-–]\s*(Extended Cut|Extended Version|Director's Cut|Uncut)\s*$", "", t, flags=re.I)
    return t.strip()


def match(cat, title, first_year):
    media = "tv" if cat == "TV" else "movie"
    pick = search(title, media, first_year)
    how = "exact"
    if not pick and clean(title) != title:
        indian = bool(re.search(r"\((Hindi|Tamil|Telugu|Malayalam|Kannada|English)\)", title, re.I))
        pick = search(clean(title), media, first_year, indian)
        how = "clean"
    if not pick:
        pick = fuzzy(clean(title), media, first_year)
        how = "fuzzy"
    if not pick:
        return None
    ja = tmdb("%s/%d" % (media, pick["id"]), language="ja-JP")
    return {"tmdb": pick["id"], "media": media, "lang": ja.get("original_language"),
            "title": ja.get("name") or ja.get("title") or title,
            "poster": ja.get("poster_path") or pick.get("poster_path"),
            "genres": [g["id"] for g in ja.get("genres", [])],
            "how": how}


def main(tsv, out, all_countries=False, if_new=False):
    rows = [r for r in csv.DictReader(open(tsv, encoding="utf-8"), delimiter="\t") if all_countries or r["country_iso2"] == "JP"]
    weeks = sorted(set(r["week"] for r in rows))
    # --if-new：今のファイルと同じ週までなら作り直さない（GitHub で毎日確かめるため）
    if if_new and os.path.exists(out):
        try:
            if json.load(open(out, encoding="utf-8")).get("updated") == weeks[-1]:
                print("新しい週なし", weeks[-1])
                return
        except Exception:
            pass
    first = {}
    for r in sorted(rows, key=lambda r: r["week"]):
        first.setdefault((r["category"], r["show_title"]), r["week"])
    keys = sorted(first)

    cache = json.load(open(CACHE, encoding="utf-8")) if os.path.exists(CACHE) else {}
    overrides = json.load(open(OVERRIDES, encoding="utf-8")) if os.path.exists(OVERRIDES) else {}
    todo = [k for k in keys if "%s|%s" % k not in cache and "%s|%s" % k not in overrides]
    print("作品", len(keys), "／新しく探す", len(todo), flush=True)

    def work(k):
        return k, match(k[0], k[1], year(first[k]))

    done = 0
    with ThreadPoolExecutor(4) as ex:
        for k, m in ex.map(work, todo):
            cache["%s|%s" % k] = m
            done += 1
            if done % 100 == 0:
                print(done, "/", len(todo), flush=True)
                json.dump(cache, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    json.dump(cache, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False, indent=0)

    # 元の言葉（日本・海外の絞り込み。2026-11-19 本人「タグで日本か海外か」）が無い物は取る
    need = [k for k, m in cache.items() if m and "lang" not in m]
    if need:
        print("元の言葉を取る", len(need), flush=True)
        def lang(k):
            m = cache[k]
            return k, tmdb("%s/%d" % (m["media"], m["tmdb"])).get("original_language")
        with ThreadPoolExecutor(4) as ex:
            for k, l in ex.map(lang, need):
                cache[k]["lang"] = l
        json.dump(cache, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False, indent=0)

    # 手で直した物
    for ck, o in overrides.items():
        if o is None:
            cache[ck] = None
        elif "title" not in o:
            ja = tmdb("%s/%d" % (o["media"], o["tmdb"]), language="ja-JP")
            cache[ck] = {"tmdb": o["tmdb"], "media": o["media"], "lang": ja.get("original_language"), "title": ja.get("name") or ja.get("title"),
                         "poster": ja.get("poster_path"), "genres": [g["id"] for g in ja.get("genres", [])], "how": "manual"}

    items, index = [], {}
    for k in keys:
        m = cache.get("%s|%s" % k)
        it = {"en": k[1], "c": "t" if k[0] == "TV" else "f"}
        if m:
            it.update({"tmdb": m["tmdb"], "media": m["media"], "title": m["title"], "poster": m["poster"], "genres": m["genres"], "lang": m.get("lang")})
        index[k] = len(items)
        items.append(it)
    wk = {}
    if all_countries:
        # 国 → 週 → 映画・シリーズ（1位から）
        for r in sorted(rows, key=lambda r: (r["country_iso2"], r["week"], r["category"], int(r["weekly_rank"]))):
            c = "t" if r["category"] == "TV" else "f"
            wk.setdefault(r["country_iso2"], {}).setdefault(r["week"], {"f": [], "t": []})[c].append(index[(r["category"], r["show_title"])])
    else:
        for w in weeks:
            d = {}
            for cat, c in (("Films", "f"), ("TV", "t")):
                rs = sorted([r for r in rows if r["week"] == w and r["category"] == cat], key=lambda r: int(r["weekly_rank"]))
                d[c] = [index[(cat, r["show_title"])] for r in rs]
            wk[w] = d
    data = {"updated": weeks[-1], "source": "Netflix Top 10 (https://www.netflix.com/tudum/top10)", "items": items, "weeks": wk}
    if all_countries:
        data["countries"] = {r["country_iso2"]: r["country_name"] for r in rows}
    json.dump(data, open(out, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))

    miss = [k for k in keys if not cache.get("%s|%s" % k)]
    fz = [k for k in keys if (cache.get("%s|%s" % k) or {}).get("how") == "fuzzy"]
    print("週", len(weeks), weeks[0], "〜", weeks[-1], "／作品", len(keys), "／合った", len(keys) - len(miss), "／合わない", len(miss), "／近い題名で合わせた", len(fz))
    for k in fz:
        print("  近い：", k[1], "→", cache["%s|%s" % k]["title"])
    print("出力", out, os.path.getsize(out) // 1024, "KB")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], "--all" in sys.argv[3:], "--if-new" in sys.argv[3:])
