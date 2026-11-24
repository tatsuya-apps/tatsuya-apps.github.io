#!/usr/bin/env python3
"""ミタヨ：アプリに出さない作品（グラビア・成人向けの映像）の一覧を毎日作る（2026-11-24）

TMDB はグラビアや AV 女優の映像の多くに「成人向け」の印が無く、探す・人の画面・関連に出てしまう（本人がアプリで見つけた）。
アプリはこの一覧にある作品を、TMDB から取った一覧（探す・人の画面・関連・ホーム）に出さない。自分で登録した作品は消さない。

仕分けの手順（上から順。理由 r。本人と決めた。仕様書「出さない作品」）：
 0. tools/mitana/hidden_keep.json にある → 消さない（普通の作品だと確かめた物）
    tools/mitana/hidden_add.json にある → 消す "manual"（決まりで拾えず、本人が見つけた物）
 1. TMDB の印と札
    "adult"：「成人向け」の印（下の人の出演作で見つかった物）
    "kw"：キーワード「gravure idol」「idol video」「image video」が付いた映画
    "person"：kw の作品の出演者（上から3人）の出演作で、題名に本人の名前、ジャンルが無いか弱い物だけ
 2. Wikidata で職業がグラビアアイドル（Q1328668）・AV 女優（Q1079215）・AV 男優（Q8380347）・
    ポルノ俳優（Q488111、日本国籍だけ）と登録され、TMDB の人の番号（P4985）がある人の出演作
    （hidden_people_keep.json の人は除く：同じ名前の別人と結び付いている人。例 萩原舞＝Hello! Project）
    "g-name"：グラビアアイドルで、題名に本人の名前、ジャンルが無いか弱い物だけ
    "av-name"：AV 女優などで、題名に本人の名前、ジャンルが無いか弱い物だけ
    "av-word"：AV 女優などで、題名に性的な言葉（SEX_WORDS）、ジャンルが無いか弱い物だけ
    "av-r18"：AV 女優などで、ジャンルが無く、映倫の区分が R18+
    消さない：AV 女優などで、ジャンルが無く、U-NEXT 以外のサブスク（日本の見放題・無料・広告つき）で配信中か映倫 G・PG12、
              ピンク映画の会社（PINK_COMPANIES）でない物（釣りバカ日誌・極道の妻たちなど脇役で出ているだけの作品）
              ※ U-NEXT は成人向けも配信しているので数えない（本人 2026-11-24）
    消さない：AV 女優などで、ジャンルが無いが、同じシリーズ（TMDB のコレクション）に普通の作品と分かっている物
              （hidden_keep.json にあるか、上の配信中・G で消さないと決まった物）が1本でもある物（ミナミの帝王など）
              ※ 「ジャンルが付いている物があれば」は甘かった（未亡人下宿などの成人向けのシリーズも残った）のでやめた
    "av-other"：AV 女優などで、ジャンルが無く、上のどれでもない物
  ジャンルが弱い＝ドキュメンタリー・ドラマ・音楽・テレビ映画だけ

出力：mitana/data/hidden.json（サイトで誰でも見られるので、題名・人の名前は入れない。2026-11-24 本人と決めた）
  {"updated": "2026-11-24", "count": 123, "items": {"movie-123": {"r": "kw"}}}
  増えた・減った番号は GitHub の変更の記録（毎日の差分）で分かる
  題名入りの表（本人が見る用）は2つめの引数に .md を渡すと手元に作る（サイトには置かない）
使い方：TMDB_KEY=<鍵> python3 -I tools/mitana/hidden.py mitana/data/hidden.json [<手元の表>.md]
名乗り（User-Agent）は Wikimedia の決まりどおり、アプリの名前とページの URL。TMDB の鍵は環境変数だけ
"""
import datetime, json, os, sys, time, unicodedata, urllib.error, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor

KEY = os.environ["TMDB_KEY"]
UA = "Mitana/1.0 (https://tatsuya-apps.github.io/mitana/)"
HERE = os.path.dirname(os.path.abspath(__file__))
KEYWORDS = {323931: "gravure idol", 382109: "idol video", 388494: "image video"}
WEAK_GENRES = {99, 18, 10402, 10770}  # ドキュメンタリー・ドラマ・音楽・テレビ映画
OCCUPATIONS = {"Q1328668": "gravure", "Q1079215": "av", "Q8380347": "av", "Q488111": "av"}
SEX_WORDS = ["痴漢", "人妻", "情事", "巨乳", "淫", "レイプ", "濡", "sex", "セックス", "ヌード", "nude", "爆乳", "乳", "犯",
             "愛欲", "不倫", "悶", "快感", "絶頂", "エロ", "肉", "性", "痴", "オナ", "本番", "監禁", "凌辱", "陵辱", "緊縛", "熟女",
             "団地妻", "宇能鴻一郎", "uno koichiro", "エマニエル", "下半身", "くりいむレモン", "花魁", "四畳半", "童貞", "狂乱", "夫人", "秘本"]
PINK_COMPANIES = {"Shintoho Pictures", "Xces Film", "Okura Pictures", "Kokuei Company", "Shishi Production", "Tôkatsu Pro",
                  "Million Film", "Nextacy", "Cinepop"}
SMALL = str.maketrans("ぁぃぅぇぉゃゅょっゎァィゥェォャュョッヮ", "あいうえおやゆよつわアイウエオヤユヨツワ")


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


def sparql(query):
    url = "https://query.wikidata.org/sparql?format=json&query=" + urllib.parse.quote(query)
    for i in range(5):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)["results"]["bindings"]
        except Exception:
            time.sleep(5 + i * 10)
    sys.exit("Wikidata に聞けなかった")


def norm(s):
    """空白を除き、全角・半角と小さい字（ぁ→あ）をそろえ、小文字に"""
    return unicodedata.normalize("NFKC", "".join((s or "").split())).translate(SMALL).lower()


def load(name):
    p = os.path.join(HERE, name)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else []


def names_of(d, extra=""):
    names = {norm(extra), norm(d.get("name"))}
    names |= {norm(a) for a in d.get("also_known_as", []) if any("぀" <= ch <= "鿿" for ch in a)}
    return {n for n in names if len(n) >= 2}


def main():
    if len(sys.argv) not in (2, 3):
        sys.exit(__doc__)
    keep = set(load("hidden_keep.json"))
    people_keep = set(load("hidden_people_keep.json"))

    items = {}

    def add(mid, title, reason):
        key = f"movie-{mid}"
        if key not in keep and key not in items:
            items[key] = {"t": title, "r": reason}

    # 0. 手で足した物
    for key in load("hidden_add.json"):
        d = get(f"movie/{key.split('-')[1]}")
        add(key.split("-")[1], (d or {}).get("title") or "", "manual")

    # 1. キーワードの札
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
    tagged_people = {}
    for mid in dict.fromkeys(tagged):
        c = get(f"movie/{mid}/credits")
        for p in (c or {}).get("cast", [])[:3]:
            tagged_people[p["id"]] = p.get("name") or ""

    # 2. Wikidata の人
    wd = {}
    for occ, kind in OCCUPATIONS.items():
        jp = "?p wdt:P27 wd:Q17 ." if occ == "Q488111" else ""
        for b in sparql(f"SELECT DISTINCT ?t WHERE {{ ?p wdt:P106 wd:{occ} ; wdt:P4985 ?t . {jp} }}"):
            t = b["t"]["value"]
            if t.isdigit() and int(t) not in people_keep:
                wd[int(t)] = "av" if kind == "av" or wd.get(int(t)) == "av" else kind

    everyone = {pid: ("tagged", name) for pid, name in tagged_people.items() if pid not in people_keep}
    for pid, kind in wd.items():
        everyone[pid] = (kind, everyone.get(pid, ("", ""))[1])

    def credits(item):
        pid, (kind, name) = item
        return pid, kind, name, get(f"person/{pid}", append_to_response="movie_credits")

    unclear = {}   # AV の人の、ジャンルが無く題名でも決まらない作品 → 区分・配信で決める
    with ThreadPoolExecutor(8) as ex:
        for pid, kind, name, d in ex.map(credits, everyone.items()):
            if not d:
                continue
            names = names_of(d, name)
            for r in d.get("movie_credits", {}).get("cast", []):
                title = r.get("title") or r.get("original_title") or ""
                if r.get("adult"):
                    add(r["id"], title, "adult")
                    continue
                g = set(r.get("genre_ids") or [])
                if not g <= WEAK_GENRES:
                    continue
                t = norm(title) + "|" + norm(r.get("original_title"))
                named = any(n in t for n in names)
                if kind == "tagged" and named:
                    add(r["id"], title, "person")
                elif kind == "gravure" and named:
                    add(r["id"], title, "g-name")
                elif kind == "av" and named:
                    add(r["id"], title, "av-name")
                elif kind == "av" and any(w in t for w in SEX_WORDS):
                    add(r["id"], title, "av-word")
                elif kind == "av" and not g:
                    unclear[r["id"]] = title

    def judge(item):
        mid, title = item
        d = get(f"movie/{mid}", append_to_response="release_dates,watch/providers")
        if not d:
            return mid, title, None, None
        certs = {x.get("certification") for g in d.get("release_dates", {}).get("results", []) if g["iso_3166_1"] == "JP"
                 for x in g["release_dates"] if x.get("certification")}
        if certs & {"R18+", "R18", "R-18"}:
            return mid, title, "av-r18", None
        jp = d.get("watch/providers", {}).get("results", {}).get("JP", {})
        services = {p["provider_name"] for k in ("flatrate", "free", "ads") for p in jp.get(k, [])}
        services = {s for s in services if not s.startswith("U-NEXT")}
        pink = {c["name"] for c in d.get("production_companies", [])} & PINK_COMPANIES
        col = None if pink else (d.get("belongs_to_collection") or {}).get("id")
        if (services or certs & {"G", "PG12"}) and not pink:
            return mid, title, "stay-svc", col
        return mid, title, "av-other", col

    todo = [(m, t) for m, t in unclear.items() if f"movie-{m}" not in items and f"movie-{m}" not in keep]
    stays = {}      # 消さないと決めた物（手元の表に出す）
    others = []
    with ThreadPoolExecutor(8) as ex:
        judged = list(ex.map(judge, todo))
    ok = {int(k.split("-")[1]) for k in keep} | {m for m, t, r, c in judged if r == "stay-svc"}
    series = {}
    for mid, title, reason, col in judged:
        if reason == "stay-svc":
            stays[mid] = (title, reason)
        elif reason == "av-other" and col:
            if col not in series:
                series[col] = any(p["id"] in ok for p in (get(f"collection/{col}") or {}).get("parts", []))
            if series[col]:
                stays[mid] = (title, "stay-series")
            else:
                add(mid, title, reason)
        elif reason:
            add(mid, title, reason)

    ordered = sorted(items.items(), key=lambda kv: int(kv[0].split("-")[1]))
    data = {"updated": datetime.date.today().isoformat(), "count": len(items),
            "items": {k: {"r": v["r"].split(":")[0]} for k, v in ordered}}
    with open(sys.argv[1], "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    if len(sys.argv) == 3:
        rows = [f"# 出さない作品の一覧（{data['updated']}・{len(items)}本）", "", "| 番号 | 理由 | 題名 |", "|---|---|---|"]
        rows += [f"| {k} | {v['r']} | {(v['t'] or '').replace('|', '／')} |" for k, v in sorted(ordered, key=lambda kv: kv[1]["r"])]
        rows += [f"| movie-{m} | {r} | {(t or '').replace('|', '／')} |" for m, (t, r) in stays.items()]
        rows += ["", "## 残す一覧（hidden_keep.json）"] + [f"- {k}" for k in sorted(keep)]
        open(sys.argv[2], "w", encoding="utf-8").write("\n".join(rows) + "\n")
    by = {}
    for v in items.values():
        k = v["r"].split(":")[0]
        by[k] = by.get(k, 0) + 1
    print(f"出さない作品 {len(items)}（{by}）・人 {len(everyone)}（Wikidata {len(wd)}）・区分と配信で決めた {len(todo)}")


if __name__ == "__main__":
    main()
