#!/usr/bin/env python3
"""ミタヨ：ホームの人気の並びを、国 × サブスクごとに毎朝まとめて作る（2026-11-28、人が増えたときに備えて）

前は iPhone ごとに TMDB の discover を聞いていた。ジャンル・年で絞っていないとき（いちばん多い形）は、
アプリはこのファイルを読むだけにする（絞ったときは今まで通り iPhone から TMDB に聞く）。

使い方：TMDB_KEY=<鍵> python3 -I tools/mitana/home.py mitana/data [国 …]   （国の初めの値は HOME_REGIONS）
出力：mitana/data/home-<国>.json
  {"updated": "2026-11-28", "region": "JP", "language": "ja-JP",
   "providers": [8, 9, ...],
   "lists": {"movie|8|all": ["movie-123", ...], "tv|8|anime": [...], ...},   ← 人気の順
   "items": {"movie-123": {"t": 題名, "o": 元の題名, "y": 年, "p": ポスター, "g": [ジャンル], "l": 元の言葉}}}
  並びの種類：all（ふだん）・ja（日本の作品）・ko（韓国の作品）・anime（シリーズのアニメだけ）
条件はアプリの TMDB.discover と同じ（新作＝映画は1年以内の公開・シリーズは1年以内に放送、評価の数の下限、ニュース・リアリティ・トークを外す）
TMDB の鍵は環境変数だけ（ファイルに書かない）
"""
import datetime, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request

KEY = os.environ["TMDB_KEY"]
HOME_REGIONS = ["JP", "US", "GB", "CA", "AU"]
JP_PROVIDERS = [8, 9, 84, 337, 15, 1884, 2683, 350]  # Netflix・Prime Video・U-NEXT・Disney+・Hulu・ABEMA・FOD・Apple TV+（views.py と同じ）
TOP_PROVIDERS = 12      # ほかの国は TMDB の表示の順で上から
JAPANESE = re.compile(r"[぀-ヿ一-鿿]")


def get(path, params, tries=4):
    q = dict(params, api_key=KEY)
    url = f"https://api.themoviedb.org/3/{path}?{urllib.parse.urlencode(q)}"
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return {}
            time.sleep(2 + i * 4)
        except Exception:
            time.sleep(2 + i * 4)
    return {}


def providers(region):
    if region == "JP":
        return JP_PROVIDERS
    res = get("watch/providers/movie", {"watch_region": region}).get("results", [])
    res.sort(key=lambda p: (p.get("display_priorities", {}).get(region, p.get("display_priority", 999))))
    return [p["provider_id"] for p in res[:TOP_PROVIDERS]]


def discover(region, lang, media, provider, kind, pages):
    today = datetime.date.today()
    year_ago = (today - datetime.timedelta(days=365)).isoformat()
    q = {"watch_region": region, "with_watch_providers": provider, "with_watch_monetization_types": "flatrate|free|ads",
         "sort_by": "popularity.desc", "include_adult": "false", "language": lang,
         "vote_count.gte": "20" if media == "movie" else "5"}
    if kind in ("ja", "ko"):
        q["with_original_language"] = kind
        q["vote_count.gte"] = "0"
    if media == "movie":
        if lang == "ja-JP":
            q["region"] = "JP"
            q["release_date.gte"] = year_ago
        else:
            q["primary_release_date.gte"] = year_ago
    else:
        q["air_date.gte"] = year_ago
        without = ["10763", "10764", "10767"]
        if kind == "anime":
            q["with_genres"] = "16"
        q["without_genres"] = ",".join(without)
    out = []
    for page in range(1, pages + 1):
        q["page"] = str(page)
        rs = get(f"discover/{media}", q).get("results", [])
        out += rs
        if len(rs) < 20:
            break
    return out


def item(r):
    title = r.get("title") or r.get("name") or ""
    orig = r.get("original_title") or r.get("original_name")
    date = r.get("release_date") or r.get("first_air_date") or ""
    d = {"t": title, "g": r.get("genre_ids") or [], "l": r.get("original_language")}
    if orig and orig != title:
        d["o"] = orig
    if date[:4].isdigit():
        d["y"] = int(date[:4])
    if r.get("poster_path"):
        d["p"] = r["poster_path"]
    return d


def build(region):
    lang = "ja-JP" if region == "JP" else "en-US"
    provs = providers(region)
    lists, items = {}, {}
    plan = [("movie", "all", 3), ("movie", "ja", 2), ("movie", "ko", 1),
            ("tv", "all", 3), ("tv", "ja", 2), ("tv", "ko", 1), ("tv", "anime", 2)]
    for p in provs:
        for media, kind, pages in plan:
            ids = []
            for r in discover(region, lang, media, p, kind, pages):
                if "id" not in r:
                    continue
                d = item(r)
                # 日本語の iPhone と同じく、日本では日本語の題名がある物だけ（日本向けに配信されている目印）
                if lang == "ja-JP" and not JAPANESE.search(d["t"]):
                    continue
                k = f"{media}-{r['id']}"
                items.setdefault(k, d)
                ids.append(k)
            lists[f"{media}|{p}|{kind}"] = ids
    return {"updated": datetime.date.today().isoformat(), "region": region, "language": lang,
            "providers": provs, "lists": lists, "items": items}


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    out_dir = sys.argv[1]
    for region in (sys.argv[2:] or HOME_REGIONS):
        data = build(region)
        path = os.path.join(out_dir, f"home-{region}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        print(f"{region}: サブスク {len(data['providers'])}・並び {len(data['lists'])}・作品 {len(data['items'])}・{os.path.getsize(path) // 1024}KB")


if __name__ == "__main__":
    main()
