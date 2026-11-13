"""Reference catalog for the demo: the materials a production planner orders
to keep a discrete-manufacturing plant running, the kinds of production line
and their run-readiness checklists, service classes, order types, the plants
and the regional materials warehouses that hold stock.

This is the single source of truth shared by the Databricks mock-dump
generator (databricks/generate_mock_dump.py imports it), the backend's
production-plan engine and the MCP tools. Part numbers, names and costs are
illustrative demo values, not any supplier's catalog.
"""
from __future__ import annotations

import hashlib

# --------------------------------------------------------------------------
# Material categories. `risk` names the demand-planning model score that
# drives how strongly materials in the category are recommended for a product.
# --------------------------------------------------------------------------
CATEGORIES = {
    "castings": {"name": "Castings & forgings", "risk": "castings_shortage_risk"},
    "resin": {"name": "Resins & polymers", "risk": "resin_shortage_risk"},
    "electronics": {"name": "Electronics & controls", "risk": "electronics_shortage_risk"},
    "metals": {"name": "Sheet, bar & coil stock", "risk": "metals_shortage_risk"},
    "motion": {"name": "Bearings, seals & hydraulics", "risk": "motion_shortage_risk"},
    "fasteners": {"name": "Fasteners & hardware", "risk": "fasteners_shortage_risk"},
    "packaging": {"name": "Packaging & labels", "risk": "packaging_shortage_risk"},
    "tooling": {"name": "Tooling & consumables", "risk": "tooling_wear_score"},
}

# SKU, name, category, purchasing unit of measure, cost per unit (USD), spec,
# tags, whether it is a kanban item (a high-runner that can go on a standing
# order), the supplier lead time in days and whether that counts as long-lead
# (the model has to order it well ahead of the build).
MATERIALS = [
    # castings & forgings
    {"sku": "MF-CST-1204", "name": "Ductile Iron Pump Housing Casting, pallet of 40", "category": "castings", "uom": "pallet",
     "unit_cost": 4120.00, "spec": "65-45-12, machined datum", "tags": ["core", "casting", "housing", "pump"], "kanban": True, "lead_time_days": 28, "long_lead": True},
    {"sku": "MF-CST-1210", "name": "Aluminum Die-Cast Valve Body, pallet of 120", "category": "castings", "uom": "pallet",
     "unit_cost": 2860.00, "spec": "A380, impregnated", "tags": ["core", "casting", "valve", "aluminum"], "kanban": True, "lead_time_days": 21, "long_lead": True},
    {"sku": "MF-CST-1260", "name": "Forged Steel Gear Blank, crate of 200", "category": "castings", "uom": "crate",
     "unit_cost": 3480.00, "spec": "8620, normalized", "tags": ["forging", "gear", "blank"], "kanban": False, "lead_time_days": 35, "long_lead": True},
    {"sku": "MF-CST-1302", "name": "Bronze Bushing Blank, box of 100", "category": "castings", "uom": "box",
     "unit_cost": 620.00, "spec": "C93200", "tags": ["bushing", "bronze"], "kanban": True, "lead_time_days": 14, "long_lead": False},
    {"sku": "MF-CST-1415", "name": "Gray Iron End Cover Casting, pallet of 80", "category": "castings", "uom": "pallet",
     "unit_cost": 1290.00, "spec": "Class 30", "tags": ["core", "casting", "cover"], "kanban": True, "lead_time_days": 21, "long_lead": True},
    # resins & polymers
    {"sku": "MF-RSN-2101", "name": "Glass-Filled Nylon PA66 Resin, 1,000 lb gaylord", "category": "resin", "uom": "gaylord",
     "unit_cost": 2340.00, "spec": "33% GF, black", "tags": ["core", "resin", "nylon", "molding"], "kanban": True, "lead_time_days": 10, "long_lead": False},
    {"sku": "MF-RSN-2110", "name": "ABS Resin, natural, 1,000 lb gaylord", "category": "resin", "uom": "gaylord",
     "unit_cost": 1640.00, "spec": "general purpose, MFI 22", "tags": ["core", "resin", "abs", "molding"], "kanban": True, "lead_time_days": 10, "long_lead": False},
    {"sku": "MF-RSN-2122", "name": "Polypropylene Copolymer, 1,000 lb gaylord", "category": "resin", "uom": "gaylord",
     "unit_cost": 1180.00, "spec": "impact grade", "tags": ["resin", "pp", "extrusion"], "kanban": True, "lead_time_days": 7, "long_lead": False},
    {"sku": "MF-RSN-2140", "name": "Black Color Masterbatch, 55 lb bag", "category": "resin", "uom": "bag",
     "unit_cost": 164.00, "spec": "2% let-down", "tags": ["colorant", "masterbatch"], "kanban": True, "lead_time_days": 7, "long_lead": False},
    {"sku": "MF-RSN-2150", "name": "TPE Overmold Compound, 55 lb bag", "category": "resin", "uom": "bag",
     "unit_cost": 420.00, "spec": "Shore 60A", "tags": ["tpe", "overmold", "grip"], "kanban": True, "lead_time_days": 14, "long_lead": False},
    # electronics & controls
    {"sku": "MF-ELC-3101", "name": "Motor Controller PCBA, tray of 25", "category": "electronics", "uom": "tray",
     "unit_cost": 3900.00, "spec": "48V, CAN bus", "tags": ["core", "pcba", "controller", "allocation"], "kanban": False, "lead_time_days": 84, "long_lead": True},
    {"sku": "MF-ELC-3120", "name": "32-bit Microcontroller, reel of 1,000", "category": "electronics", "uom": "reel",
     "unit_cost": 2750.00, "spec": "LQFP-64, automotive grade", "tags": ["mcu", "chip", "allocation"], "kanban": False, "lead_time_days": 112, "long_lead": True},
    {"sku": "MF-ELC-3133", "name": "Wire Harness, 12-circuit, box of 50", "category": "electronics", "uom": "box",
     "unit_cost": 710.00, "spec": "sealed connectors", "tags": ["core", "harness", "wiring"], "kanban": True, "lead_time_days": 21, "long_lead": False},
    {"sku": "MF-ELC-3150", "name": "Pressure Sensor, 0-250 bar, box of 40", "category": "electronics", "uom": "box",
     "unit_cost": 1850.00, "spec": "4-20 mA, M12", "tags": ["sensor", "pressure"], "kanban": False, "lead_time_days": 42, "long_lead": True},
    {"sku": "MF-ELC-3160", "name": "MLCC Capacitor Kit, 6-reel set", "category": "electronics", "uom": "set",
     "unit_cost": 260.00, "spec": "0603, X7R", "tags": ["capacitor", "smt", "passive"], "kanban": True, "lead_time_days": 28, "long_lead": False},
    # sheet, bar & coil stock
    {"sku": "MF-MTL-4101", "name": "Gear Steel Bar 8620, 2 in round, bundle", "category": "metals", "uom": "bundle",
     "unit_cost": 960.00, "spec": "12 ft lengths, 1 ton", "tags": ["core", "steel", "bar", "gear"], "kanban": True, "lead_time_days": 14, "long_lead": False},
    {"sku": "MF-MTL-4110", "name": "Cold-Rolled Steel Coil, 14 ga, 5-ton coil", "category": "metals", "uom": "coil",
     "unit_cost": 4650.00, "spec": "CS Type B, 48 in wide", "tags": ["core", "steel", "coil", "stamping"], "kanban": True, "lead_time_days": 21, "long_lead": True},
    {"sku": "MF-MTL-4124", "name": "Aluminum Sheet 5052-H32, 0.125 in, skid", "category": "metals", "uom": "skid",
     "unit_cost": 2320.00, "spec": "48 x 96 in, 25 sheets", "tags": ["aluminum", "sheet", "fabrication"], "kanban": True, "lead_time_days": 14, "long_lead": False},
    {"sku": "MF-MTL-4130", "name": "Stainless Tube 304, 1 in OD, bundle of 50", "category": "metals", "uom": "bundle",
     "unit_cost": 1640.00, "spec": "0.065 wall, 20 ft", "tags": ["stainless", "tube", "weld"], "kanban": True, "lead_time_days": 14, "long_lead": False},
    {"sku": "MF-MTL-4142", "name": "Aluminum Extrusion Billet 6063, bundle", "category": "metals", "uom": "bundle",
     "unit_cost": 3740.00, "spec": "7 in dia, homogenized", "tags": ["core", "aluminum", "billet", "extrusion"], "kanban": True, "lead_time_days": 21, "long_lead": True},
    # bearings, seals & hydraulics
    {"sku": "MF-MOT-5101", "name": "Shaft Seal Kit, Viton, box of 200", "category": "motion", "uom": "box",
     "unit_cost": 540.00, "spec": "25 mm shaft", "tags": ["core", "seal", "viton"], "kanban": True, "lead_time_days": 10, "long_lead": False},
    {"sku": "MF-MOT-5110", "name": "Ball Bearing 6205-2RS, case of 100", "category": "motion", "uom": "case",
     "unit_cost": 460.00, "spec": "25 x 52 x 15 mm, C3", "tags": ["core", "bearing"], "kanban": True, "lead_time_days": 14, "long_lead": False},
    {"sku": "MF-MOT-5120", "name": "O-Ring Assortment, Buna-N, box of 5,000", "category": "motion", "uom": "box",
     "unit_cost": 240.00, "spec": "AS568, 70 duro", "tags": ["o-ring", "seal"], "kanban": True, "lead_time_days": 7, "long_lead": False},
    {"sku": "MF-MOT-5131", "name": "Hydraulic Fitting Kit, JIC 37, box of 250", "category": "motion", "uom": "box",
     "unit_cost": 410.00, "spec": "-6 and -8, zinc-nickel", "tags": ["fitting", "hydraulic"], "kanban": True, "lead_time_days": 10, "long_lead": False},
    {"sku": "MF-MOT-5140", "name": "Gerotor Set, 12 cc, tray of 50", "category": "motion", "uom": "tray",
     "unit_cost": 2150.00, "spec": "powder metal, matched", "tags": ["gerotor", "pump"], "kanban": False, "lead_time_days": 42, "long_lead": True},
    {"sku": "MF-MOT-5152", "name": "Needle Roller Bearing, case of 200", "category": "motion", "uom": "case",
     "unit_cost": 950.00, "spec": "HK2016", "tags": ["bearing", "needle"], "kanban": True, "lead_time_days": 21, "long_lead": False},
    # fasteners & hardware
    {"sku": "MF-FST-7101", "name": "Hex Bolt M10x40, Gr 8.8, box of 500", "category": "fasteners", "uom": "box",
     "unit_cost": 190.00, "spec": "zinc flake", "tags": ["core", "bolt", "fastener"], "kanban": True, "lead_time_days": 5, "long_lead": False},
    {"sku": "MF-FST-7110", "name": "Socket Head Cap Screw M8x25, box of 1,000", "category": "fasteners", "uom": "box",
     "unit_cost": 390.00, "spec": "12.9, black oxide", "tags": ["core", "screw", "fastener"], "kanban": True, "lead_time_days": 5, "long_lead": False},
    {"sku": "MF-FST-7122", "name": "Dowel Pin 8x30, box of 1,000", "category": "fasteners", "uom": "box",
     "unit_cost": 180.00, "spec": "hardened, m6", "tags": ["dowel", "pin"], "kanban": True, "lead_time_days": 5, "long_lead": False},
    {"sku": "MF-FST-7130", "name": "Threaded Insert M6, brass, box of 2,000", "category": "fasteners", "uom": "box",
     "unit_cost": 290.00, "spec": "heat-stake", "tags": ["insert", "molding"], "kanban": True, "lead_time_days": 10, "long_lead": False},
    {"sku": "MF-FST-7141", "name": "Retaining Ring Assortment, box", "category": "fasteners", "uom": "box",
     "unit_cost": 112.00, "spec": "internal and external", "tags": ["ring", "circlip"], "kanban": True, "lead_time_days": 5, "long_lead": False},
    {"sku": "MF-FST-7150", "name": "Rivet Nut M5, steel, box of 2,500", "category": "fasteners", "uom": "box",
     "unit_cost": 268.00, "spec": "knurled, zinc", "tags": ["rivet nut", "sheet metal"], "kanban": True, "lead_time_days": 7, "long_lead": False},
    # packaging & labels
    {"sku": "MF-PKG-8101", "name": "Wood Pallet 48x40, heat-treated, stack of 20", "category": "packaging", "uom": "stack",
     "unit_cost": 310.00, "spec": "ISPM-15", "tags": ["core", "pallet", "shipping"], "kanban": True, "lead_time_days": 5, "long_lead": False},
    {"sku": "MF-PKG-8110", "name": "VCI Poly Bag 12x18, case of 500", "category": "packaging", "uom": "case",
     "unit_cost": 134.00, "spec": "4 mil, rust inhibiting", "tags": ["bag", "vci", "corrosion"], "kanban": True, "lead_time_days": 7, "long_lead": False},
    {"sku": "MF-PKG-8121", "name": "Foam End Cap Set, bundle of 100", "category": "packaging", "uom": "bundle",
     "unit_cost": 262.00, "spec": "die-cut PE", "tags": ["core", "foam", "dunnage"], "kanban": True, "lead_time_days": 10, "long_lead": False},
    {"sku": "MF-PKG-8130", "name": "Thermal Transfer Labels 4x6, case of 12 rolls", "category": "packaging", "uom": "case",
     "unit_cost": 127.00, "spec": "GS1 shipping label", "tags": ["label", "barcode"], "kanban": True, "lead_time_days": 5, "long_lead": False},
    {"sku": "MF-PKG-8144", "name": "Stretch Wrap 18 in, case of 4", "category": "packaging", "uom": "case",
     "unit_cost": 89.00, "spec": "80 ga, hand", "tags": ["wrap", "shipping"], "kanban": True, "lead_time_days": 3, "long_lead": False},
    {"sku": "MF-PKG-8150", "name": "Desiccant Pack, 2 unit, case of 600", "category": "packaging", "uom": "case",
     "unit_cost": 148.00, "spec": "clay, Tyvek", "tags": ["desiccant", "moisture"], "kanban": True, "lead_time_days": 7, "long_lead": False},
    {"sku": "MF-PKG-8162", "name": "Corrugated Shipper 16x12x10, bundle of 250", "category": "packaging", "uom": "bundle",
     "unit_cost": 140.00, "spec": "44 ECT, printed", "tags": ["core", "carton", "box"], "kanban": True, "lead_time_days": 5, "long_lead": False},
    # tooling & consumables
    {"sku": "MF-TLG-9101", "name": "Carbide Insert CNMG 432, pack of 100", "category": "tooling", "uom": "pack",
     "unit_cost": 1280.00, "spec": "coated, steel grade", "tags": ["core", "insert", "carbide", "cnc"], "kanban": True, "lead_time_days": 7, "long_lead": False},
    {"sku": "MF-TLG-9110", "name": "Coolant Concentrate, semi-synthetic, 55 gal drum", "category": "tooling", "uom": "drum",
     "unit_cost": 1290.00, "spec": "8% mix", "tags": ["coolant", "cnc"], "kanban": True, "lead_time_days": 7, "long_lead": False},
    {"sku": "MF-TLG-9120", "name": "Welding Wire ER70S-6, 0.035 in, 12 spools", "category": "tooling", "uom": "case",
     "unit_cost": 780.00, "spec": "44 lb spools", "tags": ["weld", "wire"], "kanban": True, "lead_time_days": 7, "long_lead": False},
    {"sku": "MF-TLG-9130", "name": "Powder Coat, RAL 9005 black, 55 lb box", "category": "tooling", "uom": "box",
     "unit_cost": 410.00, "spec": "polyester TGIC", "tags": ["powder", "paint", "coating"], "kanban": True, "lead_time_days": 10, "long_lead": False},
    {"sku": "MF-TLG-9140", "name": "Mold Release Spray, case of 12", "category": "tooling", "uom": "case",
     "unit_cost": 96.00, "spec": "silicone-free", "tags": ["mold release", "molding"], "kanban": True, "lead_time_days": 5, "long_lead": False},
]
MATERIAL_BY_SKU = {p["sku"]: p for p in MATERIALS}

# What each kind of production line consumes: sku -> typical purchasing units
# per week at a normal build rate. The generator derives usage and order
# history from this, with noise. `readiness` is the run-readiness checklist a
# planner walks with the line supervisor before releasing a production order;
# `coverage` is the material groups the product document tracks cover for;
# `name_words` and `unit_price` shape the finished goods the generator invents.
LINE_TYPES = {
    "cnc_machining": {"name": "CNC machining cell", "usage": {
        "MF-CST-1204": 1.0, "MF-CST-1415": 0.8, "MF-MTL-4101": 0.6, "MF-MOT-5101": 0.5, "MF-MOT-5110": 0.5, "MF-FST-7110": 0.5,
        "MF-TLG-9101": 0.4, "MF-TLG-9110": 0.25, "MF-PKG-8162": 0.6, "MF-PKG-8110": 0.4, "MF-FST-7122": 0.3, "MF-MOT-5120": 0.25},
        "readiness": ["First-article inspection signed off for this revision", "Fixtures and tooling staged, tool life checked",
                      "Castings and bar stock kitted to the cell", "CMM program and gauges available",
                      "Coolant concentration and chip handling checked", "Operators certified for this part family on shift"],
        "coverage": [("castings", "Castings & housings"), ("metals", "Bar stock"), ("motion", "Bearings & seals"),
                     ("fasteners", "Fasteners"), ("tooling", "Inserts & coolant"), ("packaging", "Packaging")],
        "name_words": ["Gear Pump", "Valve Block", "Manifold", "Pump Housing", "Spindle Housing", "Gearbox Cover"], "unit_price": (180, 640)},
    "injection_molding": {"name": "Injection molding line", "usage": {
        "MF-RSN-2101": 1.2, "MF-RSN-2110": 1.0, "MF-RSN-2140": 0.8, "MF-RSN-2150": 0.4, "MF-FST-7130": 0.5, "MF-TLG-9140": 0.4,
        "MF-PKG-8162": 0.8, "MF-PKG-8121": 0.5, "MF-PKG-8130": 0.3, "MF-PKG-8101": 0.3},
        "readiness": ["Mold pulled, cleaned and water lines verified", "Resin dried to spec and lot recorded",
                      "Process sheet loaded and first shots approved", "Regrind ratio and colorant let-down confirmed",
                      "Robot end-of-arm tooling and conveyor checked"],
        "coverage": [("resin", "Resin & colorant"), ("fasteners", "Inserts"), ("tooling", "Mold consumables"), ("packaging", "Packaging")],
        "name_words": ["Enclosure Housing", "Fan Shroud", "Connector Body", "Bezel", "Reservoir Cap", "Handle Grip"], "unit_price": (4, 38)},
    "stamping": {"name": "Stamping press line", "usage": {
        "MF-MTL-4110": 1.0, "MF-MTL-4124": 0.5, "MF-FST-7150": 0.5, "MF-TLG-9130": 0.3, "MF-PKG-8101": 0.5, "MF-PKG-8144": 0.5,
        "MF-PKG-8110": 0.4, "MF-FST-7101": 0.3},
        "readiness": ["Die set staged, sharpened and shut height recorded", "Coil certified and loaded on the decoiler",
                      "Press sensors and light curtains verified", "First-piece dimensional check signed off",
                      "Scrap chutes and part-out conveyor clear"],
        "coverage": [("metals", "Coil & sheet"), ("fasteners", "Hardware"), ("tooling", "Coatings"), ("packaging", "Packaging")],
        "name_words": ["Mounting Bracket", "Chassis Pan", "Heat Shield", "Seat Rail", "Cover Plate", "Reservoir Tank Shell"], "unit_price": (6, 55)},
    "final_assembly": {"name": "Final assembly line", "usage": {
        "MF-MOT-5110": 0.8, "MF-MOT-5101": 0.6, "MF-MOT-5131": 0.6, "MF-ELC-3133": 0.6, "MF-FST-7101": 0.8, "MF-FST-7110": 0.8,
        "MF-FST-7141": 0.4, "MF-PKG-8162": 1.0, "MF-PKG-8121": 0.6, "MF-PKG-8130": 0.4, "MF-PKG-8150": 0.3, "MF-CST-1302": 0.3},
        "readiness": ["Line-side kits complete for the first shift", "Torque tools calibrated and programs loaded",
                      "End-of-line test stand verified with the master part", "Work instructions at the current revision",
                      "Label templates and serial ranges loaded", "Staffing and cross-training matrix covers every station"],
        "coverage": [("motion", "Bearings, seals & fittings"), ("electronics", "Harnesses & sensors"), ("fasteners", "Fasteners"),
                     ("castings", "Bushings"), ("packaging", "Packaging")],
        "name_words": ["Hydraulic Power Unit", "Actuator Assembly", "Pump Assembly", "Filter Kit", "Gearbox Assembly", "Control Valve Assembly"],
        "unit_price": (220, 1900)},
    "smt_electronics": {"name": "Electronics SMT line", "usage": {
        "MF-ELC-3160": 1.0, "MF-ELC-3133": 0.6, "MF-ELC-3101": 0.3, "MF-ELC-3120": 0.25, "MF-ELC-3150": 0.3, "MF-FST-7130": 0.3,
        "MF-PKG-8110": 0.5, "MF-PKG-8150": 0.5, "MF-PKG-8130": 0.4},
        "readiness": ["Stencil and feeders set up, component lots scanned", "Solder paste within floor life",
                      "Reflow profile verified with a profiling board", "AOI program at the current revision",
                      "ESD audit and moisture-sensitive parts log checked"],
        "coverage": [("electronics", "Boards & components"), ("fasteners", "Hardware"), ("packaging", "ESD packaging")],
        "name_words": ["Motor Controller", "Sensor Module", "Display Unit", "Power Board", "Telematics Unit", "I/O Module"], "unit_price": (60, 420)},
    "welding_fab": {"name": "Welding & fabrication cell", "usage": {
        "MF-MTL-4130": 0.8, "MF-MTL-4124": 0.6, "MF-TLG-9120": 0.6, "MF-FST-7150": 0.4, "MF-FST-7101": 0.4, "MF-TLG-9130": 0.3,
        "MF-PKG-8101": 0.4, "MF-PKG-8144": 0.4},
        "readiness": ["Weld fixtures clamped and gauged", "Robot program and wire lot verified",
                      "Shielding gas supply and flow checked", "Destructive test coupon from the previous lot passed",
                      "Material certs on file for tube and sheet"],
        "coverage": [("metals", "Tube & sheet"), ("tooling", "Wire & coatings"), ("fasteners", "Hardware"), ("packaging", "Packaging")],
        "name_words": ["Reservoir Tank", "Frame Weldment", "Guard Assembly", "Exhaust Manifold", "Cart Frame", "Bracket Weldment"],
        "unit_price": (45, 380)},
    "paint_coating": {"name": "Paint & coating line", "usage": {
        "MF-TLG-9130": 1.2, "MF-PKG-8110": 0.5, "MF-PKG-8144": 0.4, "MF-FST-7141": 0.2, "MF-PKG-8101": 0.3},
        "readiness": ["Pretreatment bath titration in range", "Oven profile verified", "Racks stripped and hooks checked",
                      "Color change cleaned down and first-off panel approved"],
        "coverage": [("tooling", "Powder & pretreatment"), ("packaging", "Packaging")],
        "name_words": ["Coated Housing", "Painted Cover", "Coated Frame", "Finished Panel"], "unit_price": (12, 90)},
    "extrusion": {"name": "Extrusion line", "usage": {
        "MF-MTL-4142": 1.0, "MF-RSN-2122": 0.8, "MF-RSN-2140": 0.4, "MF-PKG-8144": 0.6, "MF-PKG-8101": 0.5, "MF-PKG-8130": 0.3,
        "MF-TLG-9140": 0.2},
        "readiness": ["Die preheated and bearing checked", "Billet or compound lot certified",
                      "Puller, saw and stretcher set to the profile", "First-length dimensional and finish check",
                      "Ageing oven schedule confirmed"],
        "coverage": [("metals", "Billet"), ("resin", "Compound"), ("packaging", "Packaging")],
        "name_words": ["Heat Sink Profile", "Rail Extrusion", "Frame Profile", "Trim Channel", "Conduit Profile"], "unit_price": (8, 70)},
    "packaging_kitting": {"name": "Packaging & kitting line", "usage": {
        "MF-PKG-8162": 1.5, "MF-PKG-8121": 0.8, "MF-PKG-8130": 0.8, "MF-PKG-8110": 0.6, "MF-PKG-8150": 0.5, "MF-PKG-8144": 0.6,
        "MF-PKG-8101": 0.6, "MF-MOT-5120": 0.3, "MF-FST-7141": 0.3},
        "readiness": ["Kit bill of materials at the current revision", "Label and carton artwork approved",
                      "Scale and vision count verified", "Pallet pattern and ship-to instructions loaded"],
        "coverage": [("packaging", "Cartons, labels & dunnage"), ("motion", "Kit seals"), ("fasteners", "Kit hardware")],
        "name_words": ["Service Kit", "Seal Kit", "Filter Kit", "Spare Parts Kit", "Install Kit"], "unit_price": (18, 160)},
}

# --------------------------------------------------------------------------
# Service classes: the fill rate a product has to hit, how many days of
# finished-goods cover it carries, and how often its plan is reviewed.
# --------------------------------------------------------------------------
SERVICE_TIERS = [
    {"code": "C", "name": "C item", "fill_rate_target_pct": 92.0, "cover_target_days": 20, "review_cadence_days": 30,
     "blurb": "Long-tail make-to-stock item. 92% fill rate, about 20 days of cover, plan reviewed monthly."},
    {"code": "B", "name": "B item", "fill_rate_target_pct": 95.0, "cover_target_days": 14, "review_cadence_days": 14,
     "blurb": "Steady runner. 95% fill rate, about 14 days of cover, plan reviewed every two weeks."},
    {"code": "A", "name": "A item", "fill_rate_target_pct": 98.0, "cover_target_days": 10, "review_cadence_days": 7,
     "blurb": "High-volume or high-margin product. 98% fill rate, about 10 days of cover, plan reviewed weekly."},
    {"code": "KEY", "name": "Key account", "fill_rate_target_pct": 99.5, "cover_target_days": 7, "review_cadence_days": 7,
     "blurb": "Line-side supply contract for an OEM program. 99.5% fill rate with penalties for a missed ship; every "
              "product in the program shares its priority."},
]
TIER_BY_CODE = {t["code"]: t for t in SERVICE_TIERS}

ORDER_TYPES = ["Scheduled run", "Expedite", "Rush order", "Pilot run"]

# Regional materials warehouses and the states they serve. Stock is held per warehouse.
WAREHOUSES = {
    "WH-NE": {"name": "Northeast Materials Warehouse", "city": "Putnam, CT", "states": ["MA", "NH", "ME", "VT", "CT", "RI"]},
    "WH-NY": {"name": "Hudson Valley Materials Warehouse", "city": "Montgomery, NY", "states": ["NY", "NJ", "PA"]},
    "WH-MA": {"name": "Mid-Atlantic Materials Warehouse", "city": "Hagerstown, MD", "states": ["MD", "VA", "DE", "DC", "WV", "NC"]},
    "WH-SE": {"name": "Southeast Materials Warehouse", "city": "Lakeland, FL", "states": ["FL", "GA", "SC", "AL", "TN"]},
    "WH-SC": {"name": "South Central Materials Warehouse", "city": "Arlington, TX", "states": ["TX", "OK", "LA", "AR", "NM"]},
    "WH-MW": {"name": "Midwest Materials Warehouse", "city": "Terre Haute, IN", "states": ["IL", "IN", "OH", "MI", "WI", "MN", "MO", "KY"]},
    "WH-W": {"name": "West Materials Warehouse", "city": "Ontario, CA", "states": ["CA", "AZ", "NV", "OR", "WA", "CO", "UT"]},
}
STATE_TO_WAREHOUSE = {st: wh for wh, d in WAREHOUSES.items() for st in d["states"]}

# The plants. One planner owns the plan for one plant. `mix` weights the kinds
# of line the generator gives a plant, in LINE_TYPES order.
PLANTS = {
    "PLT-MA-PEA": {"name": "Peabody Fluid Power Plant", "city": "Peabody", "state": "MA", "mix": [34, 6, 6, 26, 8, 6, 4, 2, 8]},
    "PLT-NY-KGN": {"name": "Kingston Molding Plant", "city": "Kingston", "state": "NY", "mix": [4, 44, 4, 14, 8, 2, 4, 8, 12]},
    "PLT-TX-FTW": {"name": "Fort Worth Assembly Plant", "city": "Fort Worth", "state": "TX", "mix": [8, 8, 14, 34, 14, 8, 6, 2, 6]},
    "PLT-PA-YRK": {"name": "York Fabrication Plant", "city": "York", "state": "PA", "mix": [10, 2, 28, 10, 2, 28, 12, 2, 6]},
    "PLT-FL-LKL": {"name": "Lakeland Packaging Plant", "city": "Lakeland", "state": "FL", "mix": [2, 20, 4, 16, 4, 2, 4, 8, 40]},
    "PLT-OH-DAY": {"name": "Dayton Machining Plant", "city": "Dayton", "state": "OH", "mix": [46, 2, 12, 18, 4, 8, 4, 2, 4]},
    "PLT-CA-FRS": {"name": "Fresno Extrusion Plant", "city": "Fresno", "state": "CA", "mix": [6, 10, 6, 12, 4, 8, 10, 38, 6]},
    "PLT-NC-GSO": {"name": "Greensboro Electronics Plant", "city": "Greensboro", "state": "NC", "mix": [2, 16, 4, 20, 44, 2, 2, 2, 8]},
}

# Why a planner has no signal where the decision gets made - the shop-floor
# reality the app is built for.
SIGNAL_NOTES = ["Press shop - steel building, no Wi-Fi", "Paint booth - RF-shielded, no signal",
                "Mezzanine stores - dead zone", "Foundry bay - handhelds drop off the network",
                "Cold dock - no coverage past the doors", "Assembly hall, weak Wi-Fi",
                "Good signal", "Good signal", "Good signal"]


def _h(*parts) -> int:
    return int(hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


def material_cost(sku: str) -> float:
    """Unit cost charged to a product's material budget for one purchasing unit."""
    return MATERIAL_BY_SKU[sku]["unit_cost"]


def initial_inventory(warehouse: str) -> dict:
    """Deterministic on-hand units per SKU at a materials warehouse. A few SKUs
    are deliberately short so shortages and follow-ups show up."""
    out = {}
    for p in MATERIALS:
        base = 24 if p["kanban"] else 8
        out[p["sku"]] = base + _h(warehouse, p["sku"]) % (base * 4)
    # Hero shortages: motor controller boards are on allocation everywhere,
    # pump housing castings are tight at the Northeast warehouse.
    out["MF-ELC-3101"] = 1 + _h(warehouse, "short") % 3
    if warehouse == "WH-NE":
        out["MF-CST-1204"] = 3
    return out


def warehouse_for_state(state: str) -> str:
    return STATE_TO_WAREHOUSE.get(state, "WH-MW")


def public_catalog() -> dict:
    """What the mobile app syncs as the `catalog` document."""
    return {
        "categories": {k: v["name"] for k, v in CATEGORIES.items()},
        "materials": MATERIALS,
        "service_tiers": SERVICE_TIERS,
        "order_types": ORDER_TYPES,
        "warehouses": {k: {"name": v["name"], "city": v["city"]} for k, v in WAREHOUSES.items()},
        "line_types": {k: v["name"] for k, v in LINE_TYPES.items()},
        "readiness_checklists": {k: v["readiness"] for k, v in LINE_TYPES.items()},
    }
