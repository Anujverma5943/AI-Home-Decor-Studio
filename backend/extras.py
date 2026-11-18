import io, math, base64, datetime as dt
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer, PageBreak, Image
from reportlab.graphics.shapes import Drawing, Rect, String

# (stage, base days, % of cost, dependency indices)
STAGES = [("Site preparation", 5, 2, []), ("Site clearing", 4, 1, [0]), ("Foundation", 25, 12, [1]),
          ("Plinth", 10, 5, [2]), ("Structural construction", 45, 22, [3]), ("Brickwork", 25, 9, [4]),
          ("Roofing", 12, 6, [4]), ("Electrical work", 15, 5, [5]), ("Plumbing", 12, 4, [5]),
          ("Plastering", 18, 6, [7, 8]), ("Flooring", 15, 6, [9]), ("Painting", 12, 4, [9, 10]),
          ("Doors and windows", 10, 5, [9]), ("Interior finishing", 25, 12, [10, 12]),
          ("Final inspection", 3, 1, [11, 13, 6])]


def timeline(p, e):
    start = dt.date.fromisoformat(p["start_date"]) if p.get("start_date") else dt.date.today()
    k = max(0.6, e["built_up"] / 1500) * (1 + 0.35 * (p["floors"] - 1))
    out, ends = [], []
    for n, d, s, deps in STAGES:
        st = max([ends[j] for j in deps], default=start)
        days = max(1, round(d * k)); en = st + dt.timedelta(days=days); ends.append(en)
        pct = p.get("stage_progress", {}).get(n, 0)
        out.append({"name": n, "days": days, "start": st.isoformat(), "end": en.isoformat(),
                    "depends": [STAGES[j][0] for j in deps], "share": s,
                    "cost": round(e["total"] * s / 100), "pct": pct,
                    "status": "Done" if pct >= 100 else "In progress" if pct > 0 else "Not started"})
    return {"stages": out, "end": max(ends).isoformat(), "days": (max(ends) - start).days,
            "overall": round(sum(o["pct"] * o["share"] for o in out) / 100, 1)}


ZONES = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
RULES = {"Kitchen": ["SE", "NW"], "Pooja": ["NE", "E", "N"], "Bedroom": ["SW", "S", "W"],
         "Living": ["N", "NE", "E"], "Bathroom": ["NW", "W", "S"]}


def vastu_solar(p, a):
    cx0, cy0 = a["off_x"] + a["fw"] / 2, a["off_y"] + a["fl"] / 2
    res = []
    for r in p["rooms"]:
        if r["kind"] not in RULES: continue
        dx, dy = r["x"] + r["w"] / 2 - cx0, -(r["y"] + r["h"] / 2 - cy0)  # top of plan = North
        z = "Center" if abs(dx) < a["fw"] * .1 and abs(dy) < a["fl"] * .1 else \
            ZONES[int(((math.degrees(math.atan2(dx, dy)) % 360) + 22.5) // 45) % 8]
        ok = z in RULES[r["kind"]]
        res.append({"room": r["name"], "floor": r["floor"] + 1, "zone": z,
                    "ideal": ", ".join(RULES[r["kind"]]), "status": "Matches" if ok else "Consider moving"})
    top = [r for r in p["rooms"] if r["floor"] == p["floors"] - 1]
    usable = sum(r["w"] * r["h"] for r in top) * 0.5
    kw = math.floor(usable / 100 * 2) / 2
    return {"vastu": res, "assumption": "Top of the plan is treated as North. Suggestions only.",
            "solar": {"usable_roof_sqft": round(usable), "capacity_kw": kw,
                      "kwh_per_year": round(kw * 1400), "sample_cost_inr": round(kw * 55000),
                      "note": "Planning estimate: ~100 sq ft per kW, ~1400 kWh/kW/yr, sample cost Rs 55,000/kW."}}


def suppliers(e, km):
    rows = []
    for r in e["boq"]:
        if r["category"] == "Material" or r["name"] in ("Flooring", "Painting"):
            for v, f in (("Sample vendor A", .97), ("Sample vendor B", 1.04)):
                tr = round(km * 25)
                rows.append({"material": r["name"], "vendor": v, "rate": round(r["rate"] * f, 1), "unit": r["unit"],
                             "transport": tr, "total": round(r["qty"] * r["rate"] * f + tr), "verified": False})
    return {"rows": rows, "warning": "SAMPLE DATA. Not real suppliers or live prices. Connect a verified source."}


def _tbl(data, widths=None):
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#15232f")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                           ("GRID", (0, 0), (-1, -1), .3, colors.grey), ("FONTSIZE", (0, 0), (-1, -1), 8)]))
    return t


def _plan(p, a, f):
    sc = 480 / a["plot_l_ft"]
    d = Drawing(500, a["plot_w_ft"] * sc + 10)
    d.add(Rect(0, 0, a["plot_l_ft"] * sc, a["plot_w_ft"] * sc, strokeDashArray=[3, 2], fillColor=None))
    for r in p["rooms"]:
        if r["floor"] != f: continue
        d.add(Rect(r["x"] * sc, (a["plot_w_ft"] - r["y"] - r["h"]) * sc, r["w"] * sc, r["h"] * sc,
                   fillColor=colors.HexColor("#cfe0ea"), strokeColor=colors.HexColor("#15232f")))
        d.add(String(r["x"] * sc + 3, (a["plot_w_ft"] - r["y"]) * sc - 10, r["name"], fontSize=7))
    return d


def build_pdf(p, a, e, tl, vs, sp, shot):
    buf, S = io.BytesIO(), getSampleStyleSheet()
    def foot(c, d):
        c.setFont("Helvetica", 8); c.drawString(40, 25, f"{p['name']} - estimate only, not a structural design"); c.drawRightString(555, 25, f"Page {d.page}")
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=40, rightMargin=40, topMargin=40, bottomMargin=45)
    m = lambda n: f"Rs {round(n):,}"
    s = [Paragraph(f"Project Report: {p['name']}", S["Title"]),
         Paragraph(f"Location: {p['location'] or '-'} | Tier: {p['tier']} | Floors: {p['floors']}", S["Normal"]), Spacer(1, 8),
         Paragraph("Plot and requirements", S["Heading2"]),
         _tbl([["Item", "Value"], ["Plot area (sq ft)", round(a["plot_area"])], ["Buildable area (sq ft)", round(a["buildable_area"])],
               ["Utilization", f"{a['utilization']*100:.0f}%"], ["Bedrooms / baths", f"{p['bedrooms']} / {p['bathrooms']}"],
               ["Extras", ", ".join(p["extras"]) or "-"], ["Built-up (sq ft)", e["built_up"]]], [200, 200]),
         Paragraph("Cost summary", S["Heading2"]),
         _tbl([["Minimum", "Average", "Maximum", "Per sq ft"], [m(e["min"]), m(e["avg"]), m(e["max"]), m(e["per_sqft"])]]),
         Paragraph(f"Budget: {m(p['budget'])}" if p["budget"] else "No budget set.", S["Normal"]), PageBreak(),
         Paragraph("Floor plans and rooms", S["Heading2"])]
    for f in range(p["floors"]):
        s += [Paragraph(f"Floor {f+1}", S["Heading3"]), _plan(p, a, f)]
    s += [_tbl([["Room", "Floor", "W x H (ft)", "Area"]] + [[r["name"], r["floor"] + 1, f"{r['w']} x {r['h']}", round(r["w"] * r["h"])] for r in p["rooms"]])]
    if shot.startswith("data:image"):
        s += [Paragraph("3D view", S["Heading2"]), Image(io.BytesIO(base64.b64decode(shot.split(",")[1])), 470, 290)]
    s += [PageBreak(), Paragraph("Bill of quantities", S["Heading2"]),
          _tbl([["Item", "Qty", "Unit", "Rate", "Amount"]] + [[r["name"], r["qty"], r["unit"], m(r["rate"]), m(r["amount"])] for r in e["boq"]]),
          Paragraph("Rates are editable sample values, not verified prices.", S["Italic"]),
          Paragraph("Supplier comparison (SAMPLE DATA)", S["Heading2"]),
          _tbl([["Material", "Vendor", "Rate", "Transport", "Total"]] + [[r["material"], r["vendor"], r["rate"], m(r["transport"]), m(r["total"])] for r in sp["rows"]]),
          PageBreak(), Paragraph("Construction timeline", S["Heading2"]),
          _tbl([["Stage", "Start", "End", "Days", "Cost", "Done"]] + [[t["name"], t["start"], t["end"], t["days"], m(t["cost"]), f"{t['pct']}%"] for t in tl["stages"]]),
          Paragraph("Budget optimization", S["Heading2"]),
          _tbl([["Tier", "Estimated total"]] + [[k.title(), m(v)] for k, v in e["by_tier"].items()]),
          Paragraph("Vastu and solar (suggestions only)", S["Heading2"]),
          _tbl([["Room", "Zone", "Ideal", "Status"]] + [[v["room"], v["zone"], v["ideal"], v["status"]] for v in vs["vastu"]]),
          Paragraph(f"Solar: ~{vs['solar']['capacity_kw']} kW, ~{vs['solar']['kwh_per_year']} kWh/yr. {vs['solar']['note']}", S["Normal"])]
    doc.build(s, onFirstPage=foot, onLaterPages=foot)
    return buf.getvalue()
