#!/usr/bin/env python3
"""ミタヨ：日本での注目度（日本語版ウィキペディアの7日間の閲覧数）を毎日まとめて数える（2026-11-23）

アプリが iPhone ごとに数えていたのを、GitHub で1日1回にまとめる（Wikimedia の量の制限と、初回の待ち時間のため）。
使い方：TMDB_KEY=<鍵> python3 -I tools/mitana/views.py mitana/data/views.json

数える作品（候補）：
- 日本の主なサブスクで見放題の人気作品（映画・ドラマ・アニメ、人気の順に10ページ、1年以内の新作は5ページ）
- 日本の映画館の上映中（7週間以内に公開）・公開予定（4か月先まで）
- Netflix トップ10（日本）の最近13週の作品（mitana/data/netflix-jp.json があれば）
手順：TMDB の番号 → Wikidata（映画 P4947・ドラマ P4983）→ 日本語版ウィキペディアのページ → 閲覧数 API（人が見た分、7日間）
出力：{"updated": "2026-11-23", "views": {"movie-123": 4567, ...}, "nopage": ["tv-456", ...]}
  nopage は日本語版のページが無い作品（アプリはこれを数え直さない）
名乗り（User-Agent）は Wikimedia の決まりどおり、アプリの名前とページの URL。TMDB の鍵は環境変数だけ（ファイルに書かない）
"""
import datetime, json, os, sys, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor

KEY = os.environ["TMDB_KEY"]
UA = "Mitana/1.0 (https://tatsuya-apps.github.io/mitana/)"
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
PROVIDERS = [8, 9, 84, 337, 15, 1884, 2683, 350]  # Netflix・Prime Video・U-NEXT・Disney+・Hulu・ABEMA・FOD・Apple TV+


def get(url, headers=None, tries=4):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=headers or {})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(2 + i * 3)
        except Exception:
            time.sleep(2 + i * 3)
    return None


def tmdb(path, **q):
    q["api_key"] = KEY
    q.setdefault("language", "ja-JP")
    return get("https://api.themoviedb.org/3/%s?%s" % (path, urllib.parse.urlencode(q))) or {}


def candidates():
    ids = set()
    today = datetime.date.today()
    year_ago = (today - datetime.timedelta(days=365)).isoformat()
    jobs = []
    for p in PROVIDERS:
        for media in ("movie", "tv"):
            base = {"watch_region": "JP", "with_watch_providers": p, "with_watch_monetization_types": "flatrate|free|ads", "sort_by": "popularity.desc"}
            for page in range(1, 11):
                jobs.append((media, dict(base, page=page)))
            new = dict(base)
            new["primary_release_date.gte" if media == "movie" else "air_date.gte"] = year_ago
            for page in range(1, 6):
                jobs.append((media, dict(new, page=page)))
    # 映画館（アプリの TMDB.theater と同じ考え方）
    now_from = (today - datetime.timedelta(days=49)).isoformat()
    up_to = (today + datetime.timedelta(days=120)).isoformat()
    th = {"region": "JP", "with_release_type": "2|3", "sort_by": "popularity.desc"}
    for page in range(1, 6):
        jobs.append(("movie", dict(th, page=page, **{"release_date.gte": now_from, "release_date.lte": today.isoformat()})))
        jobs.append(("movie", dict(th, page=page, **{"release_date.gte": today.isoformat(), "release_date.lte": up_to})))
        jobs.append(("movie", dict(th, page=page, with_original_language="ja", **{"release_date.gte": now_from, "release_date.lte": up_to})))

    def run(job):
        media, q = job
        return media, tmdb("discover/" + media, **q).get("results", [])

    with ThreadPoolExecutor(4) as ex:
        for media, results in ex.map(run, jobs):
            for r in results:
                ids.add((media, r["id"]))
    # Netflix トップ10（日本）の最近13週
    nf = os.path.join(ROOT, "mitana", "data", "netflix-jp.json")
    if os.path.exists(nf):
        d = json.load(open(nf, encoding="utf-8"))
        for w in sorted(d["weeks"])[-13:]:
            for c in ("f", "t"):
                for i in d["weeks"][w][c]:
                    it = d["items"][i]
                    if it.get("tmdb") and it.get("media"):
                        ids.add((it["media"], it["tmdb"]))
    return ids


def pages(ids):
    """(media, id) → 日本語版ウィキペディアのページ名（URL の形）"""
    out = {}
    for media in ("movie", "tv"):
        prop = "P4947" if media == "movie" else "P4983"
        mids = sorted(i for m, i in ids if m == media)
        for s in range(0, len(mids), 80):
            chunk = mids[s:s + 80]
            values = " ".join('"%d"' % i for i in chunk)
            q = "SELECT ?id ?article WHERE { VALUES ?id {%s} ?item wdt:%s ?id . ?article schema:about ?item ; schema:isPartOf <https://ja.wikipedia.org/> . }" % (values, prop)
            d = get("https://query.wikidata.org/sparql?" + urllib.parse.urlencode({"query": q, "format": "json"}),
                    {"User-Agent": UA, "Accept": "application/sparql-results+json"})
            for b in (d or {}).get("results", {}).get("bindings", []):
                try:
                    i = int(b["id"]["value"])
                except (KeyError, ValueError):
                    continue
                out[(media, i)] = b["article"]["value"].split("/wiki/")[-1]
            time.sleep(1)
    return out


def views(names):
    today = datetime.datetime.now(datetime.timezone.utc).date()
    s = (today - datetime.timedelta(days=7)).strftime("%Y%m%d")
    e = (today - datetime.timedelta(days=1)).strftime("%Y%m%d")

    def one(name):
        enc = name.replace("/", "%2F")
        d = get("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/ja.wikipedia/all-access/user/%s/daily/%s/%s" % (enc, s, e),
                {"User-Agent": UA})
        time.sleep(0.2)
        return name, (sum(x.get("views", 0) for x in d["items"]) if d and "items" in d else None)

    out = {}
    # 同時に3つまで（アプリと同じ）
    with ThreadPoolExecutor(3) as ex:
        for name, v in ex.map(one, sorted(set(names))):
            if v is not None:
                out[name] = v
    return out


def main(path):
    t0 = time.time()
    ids = candidates()
    print("候補", len(ids), flush=True)
    pg = pages(ids)
    print("日本語版のページ", len(pg), flush=True)
    vs = views(pg.values())
    result = {}
    for k, name in pg.items():
        if name in vs:
            result["%s-%d" % k] = vs[name]
    nopage = sorted("%s-%d" % k for k in ids if k not in pg)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = {"updated": datetime.date.today().isoformat(), "views": dict(sorted(result.items())), "nopage": nopage}
    json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print("閲覧数", len(result), "／ページ無し", len(nopage), "／%d秒" % (time.time() - t0), "／", os.path.getsize(path) // 1024, "KB")


if __name__ == "__main__":
    main(sys.argv[1])
