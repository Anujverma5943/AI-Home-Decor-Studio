try:
    from dotenv import load_dotenv; load_dotenv()
except ImportError:
    pass
import json, os, sqlite3, uuid, math, datetime as dt
from pathlib import Path
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

DB = os.getenv("DB_PATH", "studio.db")
app = FastAPI(title="AI Home Decor & Construction Planning Studio")
FT = 3.28084
TIERS = {"economy": 0.8, "standard": 1.0, "premium": 1.6}  # finishing-cost multipliers

# Editable SAMPLE rates (INR). Not verified market prices.
DEFAULT_RATES = {
    "cement": 400, "steel": 65, "bricks": 9, "sand": 55, "aggregate": 50,
    "foundation": 180, "labour": 450, "electrical": 120, "plumbing": 100,
    "flooring": 110, "painting": 35, "doors_windows": 90, "roofing": 60,
    "interior": 250,
}
# Quantity per sq ft built-up (documented assumptions, editable via overrides)
QTY = {"cement": 0.4, "steel": 4.0, "bricks": 8.0, "sand": 1.2, "aggregate": 1.35}
UNITS = {"cement": "bag", "steel": "kg", "bricks": "nos", "sand": "cft", "aggregate": "cft"}
MIN_SIZE = {"Living": (14, 12), "Kitchen": (10, 9), "Dining": (10, 9), "Bedroom": (12, 11),
            "Bathroom": (6, 8), "Parking": (10, 18), "Study": (9, 9), "Pooja": (5, 5),
            "Store": (6, 6), "Balcony": (4, 10), "Garden": (12, 10)}
TIER_SCALE = {"economy": 0.9, "standard": 1.0, "premium": 1.15}


class Project(BaseModel):
    name: str
    location: str = ""
    unit: str = "ft"            # "ft" or "m"
    plot_l: float
    plot_w: float
    setback: float = 5.0        # uniform setback in plot unit (user-entered, NOT verified bylaws)
    coverage: float = 0.7       # assumed ground coverage ratio
    floors: int = 1
    bedrooms: int = 2
    bathrooms: int = 2
    extras: list[str] = []      # Dining, Parking, Study, Pooja, Store, Balcony, Garden
    budget: float = 0
    tier: str = "standard"
    wall_color: str = "#e8e2d6"
    roof_color: str = "#8a5a44"
    rooms: list[dict] = []      # {id,name,kind,floor,x,y,w,h} in feet
    rates: dict = {}
    qty_override: dict = {}
    progress: int = 0
    start_date: str = ""
    stage_progress: dict = {}


def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("create table if not exists projects(id text primary key, data text, created text)")
    return c


def load(pid):
    with db() as c:
        r = c.execute("select * from projects where id=?", (pid,)).fetchone()
    if not r:
        raise HTTPException(404, "Project not found")
    d = json.loads(r["data"])
    d.update(id=r["id"], created=r["created"])
    return d


def analysis(p):
    k = FT if p["unit"] == "m" else 1
    L, W, sb = p["plot_l"] * k, p["plot_w"] * k, p["setback"] * k
    bl, bw = max(L - 2 * sb, 0), max(W - 2 * sb, 0)
    buildable = bl * bw
    foot = buildable * p["coverage"]
    s = math.sqrt(p["coverage"])
    return {"plot_l_ft": L, "plot_w_ft": W, "setback_ft": sb, "plot_area": L * W,
            "buildable_area": buildable, "footprint": foot, "fw": bl * s, "fl": bw * s,   # x-extent (along length), y-extent (along width)
            "off_x": sb + bl * (1 - s) / 2, "off_y": sb + bw * (1 - s) / 2,
            "utilization": foot / (L * W) if L * W else 0,
            "note": "Setbacks and coverage are your assumptions; verify with local authority."}


def pack(items, fw, fl, floor, ox, oy):
    rows, cur, cw = [], [], 0
    for it in items:
        if cur and cw + it[1] > fw:
            rows.append(cur); cur, cw = [], 0
        cur.append(it); cw += it[1]
    if cur: rows.append(cur)
    th = sum(max(i[2] for i in r) for r in rows) or 1
    sy, y, out = fl / th, 0, []
    for r in rows:
        sx, rh, x = fw / sum(i[1] for i in r), max(i[2] for i in r) * sy, 0
        for n, w, h, kind in r:
            out.append({"id": uuid.uuid4().hex[:6], "name": n, "kind": kind, "floor": floor,
                        "x": round(ox + x, 1), "y": round(oy + y, 1),
                        "w": round(w * sx, 1), "h": round(rh, 1)})
            x += w * sx
        y += rh
    return out


def check(p):
    if analysis(p)["buildable_area"] <= 0:
        raise HTTPException(400, "Setbacks leave no buildable area. Reduce the setback or enlarge the plot.")


def gen_layout(p, tier):
    a = analysis(p)
    sc = TIER_SCALE[tier]
    def room(kind, label=None):
        w, h = MIN_SIZE[kind]
        return (label or kind, w * sc, h * sc, kind)
    pub = [room("Living"), room("Kitchen")]
    pub += [room(e) for e in p["extras"] if e in MIN_SIZE]
    beds = [room("Bedroom", f"Bedroom {i+1}") for i in range(p["bedrooms"])]
    baths = [room("Bathroom", f"Bath {i+1}") for i in range(p["bathrooms"])]
    private = [x for pair in zip(beds + [None] * 9, baths + [None] * 9) for x in pair if x]
    floors = max(1, p["floors"])
    groups = [pub + (private if floors == 1 else [])]
    if floors > 1:
        n = floors - 1
        for f in range(n):
            groups.append(private[f::n])
    rooms = []
    for f, g in enumerate(groups):
        if g: rooms += pack(g, a["fw"], a["fl"], f, a["off_x"], a["off_y"])
    return rooms


def estimate(p, tier=None):
    tier = tier or p["tier"]
    a = analysis(p)
    rooms = p["rooms"]
    built = sum(r["w"] * r["h"] for r in rooms) * 1.1 if rooms else a["footprint"] * p["floors"]
    rate = {**DEFAULT_RATES, **p["rates"]}
    m = TIERS[tier]
    rows = []
    def add(cat, name, qty, unit, r):
        qty = p["qty_override"].get(name, qty)
        rows.append({"category": cat, "name": name, "qty": round(qty, 1), "unit": unit,
                     "rate": r, "amount": round(qty * r)})
    for key, q in QTY.items():
        add("Material", key.title(), built * q, UNITS[key], rate[key])
    for key in ("foundation", "labour", "electrical", "plumbing", "roofing"):
        add("Work", key.title(), built, "sq ft", rate[key])
    for key in ("flooring", "painting", "doors_windows", "interior"):
        add("Finishes", key.replace("_", " & ").title(), built, "sq ft", rate[key] * m)
    sub = sum(r["amount"] for r in rows)
    add("Other", "Transportation (3%)", 1, "lot", sub * 0.03)
    add("Other", "Other expenses (5%)", 1, "lot", sub * 0.05)
    total = sum(r["amount"] for r in rows)
    return {"built_up": round(built), "boq": rows, "total": total, "min": round(total * 0.9),
            "max": round(total * 1.15), "avg": round(total * 1.025),
            "per_sqft": round(total / built) if built else 0}


@app.get("/api/projects")
def list_projects():
    with db() as c:
        rows = c.execute("select id,data,created from projects order by created desc").fetchall()
    out = []
    for r in rows:
        d = json.loads(r["data"]); e = estimate(d)
        out.append({"id": r["id"], "name": d["name"], "location": d["location"],
                    "created": r["created"], "plot_area": round(analysis(d)["plot_area"]),
                    "built_up": e["built_up"], "total": e["total"], "progress": d["progress"]})
    return out


@app.post("/api/projects")
def create(p: Project):
    pid, now = uuid.uuid4().hex[:8], dt.date.today().isoformat()
    d = p.model_dump()
    check(d)
    d["rooms"] = gen_layout(d, d["tier"])
    with db() as c:
        c.execute("insert into projects values(?,?,?)", (pid, json.dumps(d), now))
    return load(pid)


@app.get("/api/projects/{pid}")
def get(pid: str):
    return load(pid)


@app.put("/api/projects/{pid}")
def update(pid: str, p: Project):
    load(pid)
    check(p.model_dump())
    with db() as c:
        c.execute("update projects set data=? where id=?", (json.dumps(p.model_dump()), pid))
    return load(pid)


@app.delete("/api/projects/{pid}")
def delete(pid: str):
    load(pid)
    with db() as c:
        c.execute("delete from projects where id=?", (pid,))
    return {"deleted": pid}


@app.post("/api/projects/{pid}/layout/{tier}")
def relayout(pid: str, tier: str):
    if tier not in TIERS: raise HTTPException(400, "tier must be economy, standard or premium")
    d = load(pid); d["tier"] = tier; d["rooms"] = gen_layout(d, tier)
    with db() as c:
        c.execute("update projects set data=? where id=?", (json.dumps(d), pid))
    return d


@app.get("/api/projects/{pid}/estimate")
def get_estimate(pid: str):
    d = load(pid); e = estimate(d)
    by_tier = {t: estimate(d, t)["total"] for t in TIERS}
    opt = None
    if d["budget"] > 0:
        cheaper = min(by_tier, key=by_tier.get)
        opt = {"budget": d["budget"], "over_by": max(0, e["total"] - d["budget"]),
               "utilization": round(e["total"] / d["budget"], 3),
               "cheapest_tier": cheaper, "cheapest_total": by_tier[cheaper],
               "saving_vs_current": e["total"] - by_tier[cheaper],
               "note": "Only finishing grades change; structural quantities are untouched."}
    warns = [f"{r['name']} (floor {r['floor']+1}) is only {r['w']:.0f} x {r['h']:.0f} ft, smaller than the usual minimum for a {r['kind']}."
             for r in d["rooms"] if r["kind"] in MIN_SIZE and min(r["w"], r["h"]) < 0.7 * min(MIN_SIZE[r["kind"]])]
    if warns: warns.append("Fix: lower the setback, raise coverage, remove extras, or add a floor.")
    return {"analysis": analysis(d), "warnings": warns, **e, "by_tier": by_tier, "optimizer": opt,
            "disclaimer": "Estimate only. Rates are editable samples, not verified prices."}


class ImgReq(BaseModel):
    prompt: str
    n: int = 2


@app.post("/api/ai/image")
async def ai_image(r: ImgReq):
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise HTTPException(503, "AI image generation is not configured. Set OPENAI_API_KEY in .env.")
    async with httpx.AsyncClient(timeout=120) as c:
        res = await c.post("https://api.openai.com/v1/images/generations",
                           headers={"Authorization": f"Bearer {key}"},
                           json={"model": os.getenv("IMAGE_MODEL", "gpt-image-1"),
                                 "prompt": r.prompt, "n": min(max(r.n, 1), 4), "size": "1024x1024"})
    if res.status_code != 200:
        raise HTTPException(502, f"Image service error: {res.text[:300]}")
    return {"images": [f"data:image/png;base64,{i['b64_json']}" for i in res.json()["data"]]}


import extras
from fastapi.responses import Response


@app.get("/api/projects/{pid}/timeline")
def r_timeline(pid: str):
    d = load(pid)
    return extras.timeline(d, estimate(d))


@app.get("/api/projects/{pid}/vastu")
def r_vastu(pid: str):
    d = load(pid)
    return extras.vastu_solar(d, analysis(d))


@app.get("/api/projects/{pid}/suppliers")
def r_suppliers(pid: str, km: float = 20):
    return extras.suppliers(estimate(load(pid)), max(km, 0))


class Rep(BaseModel):
    shot: str = ""


@app.post("/api/projects/{pid}/report")
def r_report(pid: str, b: Rep):
    d = load(pid); e = estimate(d); a = analysis(d)
    e["by_tier"] = {t: estimate(d, t)["total"] for t in TIERS}
    pdf = extras.build_pdf(d, a, e, extras.timeline(d, e), extras.vastu_solar(d, a), extras.suppliers(e, 20), b.shot)
    return Response(pdf, media_type="application/pdf")


app.mount("/", StaticFiles(directory=Path(__file__).parent.parent / "frontend", html=True), name="web")
