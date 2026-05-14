"""
KG Data Exporter — Streamlit Dashboard
========================================
Pulls award-level records from USAspending.gov for KG-1 construction.

Filters: parent agency × fiscal year × recipient type (higher-ed / company)
Output: award-level CSV ready for Neo4j loader

Designed for the IP Exposure Score paper (IEEE Access).
"""

import streamlit as st
import requests
import pandas as pd
import time
from io import StringIO

# ════════════════════════════════════════════════════════════════════
# CONFIG
# ════════════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="KG Data Exporter",
    page_icon="📊",
    layout="wide",
)

API_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"

AGENCIES = [
    "Department of Defense",
    "Department of Energy",
    "Department of Health and Human Services",
    "Department of Homeland Security",
    "National Science Foundation",
]

PARENT_CODES = {
    "Department of Defense": "097",
    "Department of Energy": "089",
    "Department of Health and Human Services": "075",
    "Department of Homeland Security": "070",
    "National Science Foundation": "NSF",
}

HIGHER_ED_TYPES = [
    "higher_education",
    "public_institution_of_higher_education",
    "private_institution_of_higher_education",
    "minority_serving_institution_of_higher_education",
    #"school_of_forestry",
    #"veterinary_college",
]

COMPANY_TYPES = [
    "for_profit_organization",
    "small_business",
    "other_than_small_business",
    "corporate_entity_tax_exempt",
    "corporate_entity_not_tax_exempt",
]

ASSISTANCE_FIELDS = [
    "Award ID", "Recipient Name", "Recipient UEI",
    "Awarding Agency", "Awarding Agency Code", "Awarding Sub Agency",
    "Award Amount", "Total Obligated Amount",
    "Start Date", "Award Type",
    "Recipient Location State Code", "Recipient Location Country Name",
]

CONTRACT_FIELDS = [
    "Award ID", "Recipient Name", "Recipient UEI",
    "Awarding Agency", "Awarding Agency Code", "Awarding Sub Agency",
    "Award Amount", "Total Obligated Amount",
    "Start Date", "Contract Award Type",
    "Recipient Location State Code", "Recipient Location Country Name",
]

PAGE_LIMIT = 100
TIMEOUT = 90
MAX_RETRIES = 3


# ════════════════════════════════════════════════════════════════════
# FETCH LOGIC
# ════════════════════════════════════════════════════════════════════

def _award_codes(group):
    return ["02", "03", "04", "05"] if group == "grants" else ["A", "B", "C", "D"]


def fetch_page(payload):
    """Single page fetch with retry + exponential backoff."""
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.post(API_URL, json=payload, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json(), None
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(3 * (attempt + 1))   # 3s, 6s, 9s
            else:
                return None, str(e)
    return None, "max retries exceeded"


def fetch_slice(agency, fy, recipient_types, group, fields, max_pages, progress_cb=None):
    """Pull all award pages for one agency × FY × slice × group."""
    results = []
    fy_start = f"{fy-1}-10-01"
    fy_end = f"{fy}-09-30"
    page = 1
    errors = []

    while page <= max_pages:
        payload = {
            "filters": {
                "time_period": [{"start_date": fy_start, "end_date": fy_end}],
                "agencies": [{"type": "awarding", "tier": "toptier", "name": agency}],
                "recipient_type_names": recipient_types,
                "award_type_codes": _award_codes(group),
            },
            "fields": fields,
            "page": page,
            "limit": PAGE_LIMIT,
            "sort": "Award Amount",
            "order": "desc",
        }

        data, err = fetch_page(payload)
        if err:
            errors.append(f"page {page}: {err}")
            page += 1
            continue

        rows = data.get("results", [])
        if not rows:
            break
        results.extend(rows)

        if progress_cb:
            progress_cb(len(results))

        if not data.get("page_metadata", {}).get("hasNext", False):
            break
        page += 1
        time.sleep(0.2)

    return results, errors


def normalize(rec, parent_agency, fy, recipient_slice, group):
    return {
        "parent_agency_name": parent_agency,
        "parent_agency_code": PARENT_CODES.get(parent_agency, ""),
        "awarding_agency_name": rec.get("Awarding Sub Agency") or rec.get("Awarding Agency"),
        "awarding_agency_code": rec.get("Awarding Agency Code", ""),
        "recipient_name": rec.get("Recipient Name"),
        "recipient_uei": rec.get("Recipient UEI"),
        "recipient_country": rec.get("Recipient Location Country Name"),
        "recipient_state": rec.get("Recipient Location State Code"),
        "recipient_type": recipient_slice,
        "award_id": rec.get("Award ID"),
        "obligation_usd": rec.get("Total Obligated Amount") or rec.get("Award Amount") or 0,
        "fiscal_year": fy,
        "action_date": rec.get("Start Date"),
        "award_type": rec.get("Award Type") or rec.get("Contract Award Type"),
        "award_group": group,
    }


# ════════════════════════════════════════════════════════════════════
# UI
# ════════════════════════════════════════════════════════════════════

st.title("📊 KG Data Exporter")
st.caption(
    "Award-level export from USAspending.gov for knowledge-graph construction. "
    "Live API, retry-safe, paginated."
)

# ── Sidebar filters ────────────────────────────────────────────────
st.sidebar.header("Filters")

selected_agencies = st.sidebar.multiselect(
    "Parent agencies",
    options=AGENCIES,
    default=AGENCIES,
)

fy_range = st.sidebar.slider(
    "Fiscal year range",
    min_value=2020, max_value=2026,
    value=(2020, 2026),
)

recipient_slice = st.sidebar.radio(
    "Recipient type",
    options=["Higher Education", "Companies"],
    index=0,
)

if recipient_slice == "Higher Education":
    recipient_types = HIGHER_ED_TYPES
    slice_label = "higher_education"
    award_groups = ["grants"]
else:
    recipient_types = COMPANY_TYPES
    slice_label = "company"
    award_groups = st.sidebar.multiselect(
        "Award groups for companies",
        options=["grants", "contracts"],
        default=["grants", "contracts"],
    )

max_pages = st.sidebar.number_input(
    "Max pages per slice (×100 = max records)",
    min_value=10, max_value=2000, value=700, step=50,
    help="700 pages = 70,000 records per agency × FY × group. Raise for NIH-heavy pulls.",
)

st.sidebar.markdown("---")
run_btn = st.sidebar.button("🚀 Fetch data", type="primary", use_container_width=True)

# ── Main panel ─────────────────────────────────────────────────────

if not run_btn:
    st.info(
        "Configure filters in the sidebar, then click **Fetch data**. "
        "The download button appears once the pull completes."
    )
    st.markdown("### Expected runtime")
    st.markdown(
        "- Higher-ed, all 5 agencies, FY2020–2026: **~10–15 min**\n"
        "- Companies, all 5 agencies + grants + contracts: **~25–40 min**\n"
        "- Larger DoD/HHS pulls may take longer due to volume"
    )
    st.stop()

# ── Run fetch ──────────────────────────────────────────────────────

fy_list = list(range(fy_range[0], fy_range[1] + 1))
total_slices = len(selected_agencies) * len(fy_list) * len(award_groups)

progress = st.progress(0.0, text="Starting...")
log_area = st.empty()
all_rows = []
all_errors = []
done = 0
log_lines = []

start_time = time.time()

for agency in selected_agencies:
    for fy in fy_list:
        for group in award_groups:
            fields = ASSISTANCE_FIELDS if group == "grants" else CONTRACT_FIELDS

            slice_label_pretty = f"{agency[:30]:<30} FY{fy} {group}"
            recs, errs = fetch_slice(
                agency, fy, recipient_types, group, fields, max_pages
            )
            for r in recs:
                all_rows.append(normalize(r, agency, fy, slice_label, group))

            done += 1
            progress.progress(
                done / total_slices,
                text=f"[{done}/{total_slices}] {slice_label_pretty} → {len(recs):,} records"
            )

            status = "✓" if not errs else f"⚠ {len(errs)} retry-exhausted pages"
            log_lines.append(f"{status} {slice_label_pretty} → {len(recs):,} records")
            if errs:
                all_errors.extend([f"{agency} FY{fy} {group} → {e}" for e in errs])

            log_area.code("\n".join(log_lines[-15:]), language=None)

elapsed = time.time() - start_time
progress.progress(1.0, text=f"Done in {elapsed/60:.1f} min")

# ── Results ────────────────────────────────────────────────────────

if not all_rows:
    st.error("No records retrieved. Check filters and API status.")
    st.stop()

df = pd.DataFrame(all_rows)

st.success(f"✅ Retrieved **{len(df):,}** award records in {elapsed/60:.1f} min")

# Summary stats
col1, col2, col3, col4 = st.columns(4)
col1.metric("Total rows", f"{len(df):,}")
col2.metric("Unique recipients (UEI)", f"{df['recipient_uei'].nunique():,}")
col3.metric("Unique awarding agencies", df['awarding_agency_name'].nunique())
col4.metric("Total obligations", f"${df['obligation_usd'].astype(float).sum()/1e9:.2f}B")

# Per-agency breakdown
st.markdown("### Obligations by parent agency")
agency_summary = (
    df.groupby('parent_agency_name')
      .agg(records=('award_id', 'count'),
           obligations_b=('obligation_usd', lambda x: x.astype(float).sum()/1e9))
      .round(2)
      .sort_values('obligations_b', ascending=False)
)
st.dataframe(agency_summary, use_container_width=True)

# Per-FY breakdown
st.markdown("### Obligations by fiscal year (billions)")
fy_summary = (
    df.groupby(['fiscal_year', 'parent_agency_name'])['obligation_usd']
      .apply(lambda x: round(x.astype(float).sum()/1e9, 2))
      .unstack(fill_value=0)
)
st.dataframe(fy_summary, use_container_width=True)

# Error log
if all_errors:
    with st.expander(f"⚠ {len(all_errors)} retry-exhausted pages (not in CSV)"):
        st.code("\n".join(all_errors), language=None)

# ── Download ───────────────────────────────────────────────────────
st.markdown("### Download")

csv_buf = StringIO()
df.to_csv(csv_buf, index=False)
filename = f"awards_{slice_label}_FY{fy_range[0]}-{fy_range[1]}.csv"

st.download_button(
    label=f"⬇ Download {filename}",
    data=csv_buf.getvalue(),
    file_name=filename,
    mime="text/csv",
    type="primary",
    use_container_width=True,
)

st.markdown("### Preview (first 20 rows)")
st.dataframe(df.head(20), use_container_width=True)
