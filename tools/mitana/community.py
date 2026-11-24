#!/usr/bin/env python3
"""ミタヨ：みんなのデータを数える（2026-11-24）

アプリが CloudKit の公開データベースに送った記録（MitanaWork・MitanaPick）を全部読み、
作品ごとの人数・★・最近見た人数と、「どっちが好き？」の勝ち負けを数えて JSON にする。

使い方：
  CK_KEY_ID=<鍵の番号> CK_KEY="<秘密鍵の PEM>" python3 -I tools/mitana/community.py mitana/data/community.json
  CK_ENV=development にすると開発用の入れ物を読む（初めは production）

決まり（プライバシーポリシー「3. みんなのランキング」）：
- 5人未満の数は出さない（作品・★・サブスク・勝ち負け、どれも）
- 個人の記録はそのまま出さない。出すのは数えた結果だけ
鍵は環境変数だけ（ファイルに書かない）。署名は openssl（追加の部品なし）

出力：
{"updated": "2026-11-24", "min": 5,
 "regions": {"ALL": {...}, "JP": {...}},   ← 国ごと（App Store の国）と全部
   各地域：
   "works": {"movie-123": {"n": 人数, "watched": 12, "watching": 3, "want": 20, "dropped": 1,
                           "rating": 4.1, "ratings": 9, "recent": 6, "services": {"p8": 7}}},
   "now":  ["movie-123", ...]   最近4週に見た・進めた人の多い順（30まで）
   "best": ["movie-123", ...]   ★の高い順（★を付けた人が5人以上。人数の少ない作品は平均に寄せる）
   "picks": {"movie-123": [勝ち, 負け, 同じくらい]}   比べられた回数が5以上
}
"""
import base64, collections, datetime, hashlib, json, os, subprocess, sys, tempfile, time, urllib.error, urllib.request

CONTAINER = "iCloud.com.tatsuya.Mitana"
MIN = 5          # これより少ない人数は出さない
RECENT_WEEKS = 4
TOP = 30


class CloudKit:
    def __init__(self, key_id, pem, env):
        self.key_id = key_id
        self.env = env
        fd, self.pem_path = tempfile.mkstemp(suffix=".pem")
        with os.fdopen(fd, "w") as f:
            f.write(pem.strip() + "\n")

    def close(self):
        os.remove(self.pem_path)

    def post(self, op, body):
        sub = f"/database/1/{CONTAINER}/{self.env}/public/{op}"
        data = json.dumps(body).encode()
        date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        msg = f"{date}:{base64.b64encode(hashlib.sha256(data).digest()).decode()}:{sub}"
        sig = subprocess.run(["openssl", "dgst", "-sha256", "-sign", self.pem_path],
                             input=msg.encode(), capture_output=True, check=True).stdout
        req = urllib.request.Request("https://api.apple-cloudkit.com" + sub, data=data, method="POST", headers={
            "Content-Type": "application/json",
            "X-Apple-CloudKit-Request-KeyID": self.key_id,
            "X-Apple-CloudKit-Request-ISO8601Date": date,
            "X-Apple-CloudKit-Request-SignatureV1": base64.b64encode(sig).decode(),
        })
        for i in range(4):
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                text = e.read().decode(errors="replace")
                if e.code in (429, 500, 503) and i < 3:
                    time.sleep(3 + i * 5)
                    continue
                sys.exit(f"CloudKit {e.code}: {text[:500]}")

    def all_records(self, rtype):
        out, marker = [], None
        while True:
            body = {"query": {"recordType": rtype}, "resultsLimit": 200}
            if marker:
                body["continuationMarker"] = marker
            res = self.post("records/query", body)
            for r in res.get("records", []):
                if "serverErrorCode" in r:
                    continue
                out.append({k: v.get("value") for k, v in r.get("fields", {}).items()})
            marker = res.get("continuationMarker")
            if not marker:
                return out


def recent_weeks(today):
    weeks = set()
    for i in range(RECENT_WEEKS):
        y, w, _ = (today - datetime.timedelta(weeks=i)).isocalendar()
        weeks.add(f"{y:04d}-W{w:02d}")
    return weeks


def count_region(works, picks, recent):
    stat = collections.defaultdict(lambda: {"n": 0, "watched": 0, "watching": 0, "want": 0, "dropped": 0,
                                            "rsum": 0.0, "ratings": 0, "recent": 0, "services": collections.Counter()})
    for r in works:
        key, status = r.get("work"), r.get("status")
        if not key or status not in ("watched", "watching", "want", "dropped"):
            continue
        s = stat[key]
        s["n"] += 1
        s[status] += 1
        if isinstance(r.get("rating"), (int, float)) and r["rating"] > 0:
            s["rsum"] += r["rating"]
            s["ratings"] += 1
        if status in ("watched", "watching") and r.get("week") in recent:
            s["recent"] += 1
        if r.get("service"):
            s["services"][r["service"]] += 1

    # ★の高い順：人数の少ない作品は全体の平均に寄せる（C 人分の平均を足す）
    rated = [s for s in stat.values() if s["ratings"] >= MIN]
    mean = sum(s["rsum"] for s in rated) / max(1, sum(s["ratings"] for s in rated))
    C = MIN

    out = {}
    for key, s in stat.items():
        if s["n"] < MIN:
            continue
        o = {"n": s["n"]}
        for k in ("watched", "watching", "want", "dropped", "recent"):
            if s[k] >= MIN:
                o[k] = s[k]
        if s["ratings"] >= MIN:
            o["rating"] = round(s["rsum"] / s["ratings"], 2)
            o["ratings"] = s["ratings"]
            o["_score"] = (C * mean + s["rsum"]) / (C + s["ratings"])
        sv = {k: v for k, v in s["services"].most_common() if v >= MIN}
        if sv:
            o["services"] = sv
        out[key] = o

    now = sorted((k for k, o in out.items() if o.get("recent")), key=lambda k: (-out[k]["recent"], -out[k]["n"]))[:TOP]
    best = sorted((k for k, o in out.items() if "_score" in o), key=lambda k: -out[k]["_score"])[:TOP]
    for o in out.values():
        o.pop("_score", None)

    wl = collections.defaultdict(lambda: [0, 0, 0])
    for p in picks:
        a, b = p.get("winner"), p.get("loser")
        if not a or not b:
            continue
        if p.get("tie") == 1:
            wl[a][2] += 1
            wl[b][2] += 1
        else:
            wl[a][0] += 1
            wl[b][1] += 1
    pk = {k: v for k, v in wl.items() if sum(v) >= MIN}
    return {"works": out, "now": now, "best": best, "picks": pk}


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    ck = CloudKit(os.environ["CK_KEY_ID"], os.environ["CK_KEY"], os.environ.get("CK_ENV", "production"))
    try:
        works = ck.all_records("MitanaWork")
        picks = ck.all_records("MitanaPick")
    finally:
        ck.close()
    today = datetime.date.today()
    recent = recent_weeks(today)
    regions = {"ALL": count_region(works, picks, recent)}
    for c in sorted({r.get("country") for r in works if r.get("country")}):
        reg = count_region([r for r in works if r.get("country") == c], [p for p in picks if p.get("country") == c], recent)
        if reg["works"] or reg["picks"]:
            regions[c] = reg
    data = {"updated": today.isoformat(), "min": MIN, "regions": regions}
    with open(sys.argv[1], "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print(f"記録 {len(works)} 件・比べた結果 {len(picks)} 件 → 出した作品 {len(regions['ALL']['works'])}")


if __name__ == "__main__":
    main()
