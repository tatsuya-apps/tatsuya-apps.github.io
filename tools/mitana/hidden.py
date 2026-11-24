#!/usr/bin/env python3
"""ミタヨ：アプリに出さない作品（グラビア・成人向けの映像）の一覧を毎日作る（2026-11-24）

TMDB はグラビアや AV 女優の映像の多くに「成人向け」の印が無く、探す・人の画面・関連に出てしまう（本人がアプリで見つけた）。
アプリはこの一覧にある作品を、TMDB から取った一覧（探す・人の画面・関連・ホーム）に出さない。自分で登録した作品は消さない。

決まり（理由 r）：
- "kw:<キーワード>"：TMDB のキーワード「gravure idol」「idol video」「image video」が付いた映画
- "adult"：上の作品・人の出演作のうち「成人向け」の印がある物
- "person:<名前>"：上の作品に出ている人（上から3人）の出演作で、題名にその人の名前が入り、ジャンルが無いか
  ドキュメンタリー・ドラマ・音楽・テレビ映画だけの物（「○○（名前）＋副題」の形のイメージビデオ）
- 間違って消えた作品は tools/mitana/hidden_keep.json（["movie-123", ...]）に入れると消さない

出力：mitana/data/hidden.json（サイトで誰でも見られるので、題名・人の名前は入れない。2026-11-24 本人と決めた）
  {"updated": "2026-11-24", "count": 123, "items": {"movie-123": {"r": "kw"}}}   r は kw・adult・person
  増えた・減った番号は GitHub の変更の記録（毎日の差分）で分かる
  題名入りの表（本人が見る用）は2つめの引数に .md を渡すと手元に作る（サイトには置かない）
使い方：TMDB_KEY=<鍵> python3 -I tools/mitana/hidden.py mitana/data/hidden.json [<手元の表>.md]
"""
import datetime, json, os, sys, time, urllib.error, urllib.parse, urllib.request

KEY = os.environ["TMDB_KEY"]
HERE = os.path.dirname(os.path.abspath(__file__))
KEYWORDS = {323931: "gravure idol", 382109: "idol video", 388494: "image video"}
WEAK_GENRES = {99, 18, 10402, 10770}  # ドキュメンタリー・ドラマ・音楽・テレビ映画


def get(path, **q):
    q = {"api_key": KEY, "language": "ja-JP", **q}
    url = f"https://api.themoviedb.org/3/{path}?{urllib.parse.urlencode(q)}"
    for i in range(4):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(2 + i * 3)
        except Exception:
            time.sleep(2 + i * 3)
    return None


def squash(s):
    return "".join((s or "").split()).replace("　", "")


def main():
    if len(sys.argv) not in (2, 3):
        sys.exit(__doc__)
    keep = set()
    kp = os.path.join(HERE, "hidden_keep.json")
    if os.path.exists(kp):
        keep = set(json.load(open(kp, encoding="utf-8")))

    items = {}

    def add(mid, title, reason):
        key = f"movie-{mid}"
        if key not in keep and key not in items:
            items[key] = {"t": title, "r": reason}

    tagged = []
    for kid, kname in KEYWORDS.items():
        page = 1
        while True:
            d = get("discover/movie", with_keywords=kid, include_adult="true", page=page)
            if not d:
                break
            for r in d["results"]:
                add(r["id"], r.get("title") or r.get("original_title"), f"kw:{kname}")
                tagged.append(r["id"])
            if page >= d.get("total_pages", 1) or page >= 20:
                break
            page += 1

    people = {}
    for mid in dict.fromkeys(tagged):
        c = get(f"movie/{mid}/credits")
        for p in (c or {}).get("cast", [])[:3]:
            people[p["id"]] = p.get("name") or ""

    for pid, name in people.items():
        d = get(f"person/{pid}", append_to_response="movie_credits")
        if not d:
            continue
        names = {squash(name), squash(d.get("name"))}
        names |= {squash(a) for a in d.get("also_known_as", []) if any("぀" <= ch <= "鿿" for ch in a)}
        names = {n for n in names if len(n) >= 2}
        for r in d.get("movie_credits", {}).get("cast", []):
            title = r.get("title") or r.get("original_title") or ""
            if r.get("adult"):
                add(r["id"], title, "adult")
                continue
            t = squash(title) + "|" + squash(r.get("original_title"))
            if set(r.get("genre_ids") or []) <= WEAK_GENRES and any(n in t for n in names):
                add(r["id"], title, f"person:{d.get('name') or name}")

    ordered = sorted(items.items(), key=lambda kv: int(kv[0].split("-")[1]))
    data = {"updated": datetime.date.today().isoformat(), "count": len(items),
            "items": {k: {"r": v["r"].split(":")[0]} for k, v in ordered}}
    with open(sys.argv[1], "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    if len(sys.argv) == 3:
        rows = [f"# 出さない作品の一覧（{data['updated']}・{len(items)}本）", "", "| 番号 | 理由 | 題名 |", "|---|---|---|"]
        rows += [f"| {k} | {v['r']} | {(v['t'] or '').replace('|', '／')} |" for k, v in sorted(ordered, key=lambda kv: kv[1]["r"])]
        rows += ["", "## 残す一覧（hidden_keep.json）"] + [f"- {k}" for k in sorted(keep)]
        open(sys.argv[2], "w", encoding="utf-8").write("\n".join(rows) + "\n")
    by = {}
    for v in items.values():
        k = v["r"].split(":")[0]
        by[k] = by.get(k, 0) + 1
    print(f"出さない作品 {len(items)}（{by}）・人 {len(people)}")


if __name__ == "__main__":
    main()
