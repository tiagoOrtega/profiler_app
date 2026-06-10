"""DataLens — Help page. Feature overview and quick-start guide."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st


st.title("❓ Help & Documentation")
st.caption("DataLens — multi-platform data intelligence, native Snowflake")

# ── What is DataLens ──────────────────────────────────────────────────────────

st.markdown("""
## What is DataLens?

**DataLens** is a data intelligence platform that runs natively inside Snowflake.
It automatically profiles your tables, detects data quality issues, finds relationships
between columns, computes correlation matrices, and applies ML clustering — all without
moving data out of Snowflake.

AI-powered descriptions and insights are generated via **Snowflake Cortex**, so no
external LLM service is required.
""")

st.divider()

# ── Quick start ────────────────────────────────────────────────────────────────

st.subheader("Quick Start")

col1, col2, col3 = st.columns(3)

with col1:
    st.markdown("""
    **1. Profile a Table**

    Navigate to **Profile** in the sidebar.

    - Pick a Database → Schema → Table
    - Click **Run Profile**
    - Results are saved to `DATALENS.METADATA`
    and reload automatically on every visit
    """)

with col2:
    st.markdown("""
    **2. Explore the Report**

    After profiling, open **Report**.

    - **Overview** — row count, column types, null rate histogram
    - **Columns** — per-column stats with alert highlighting
    - **Relationships** — FK-like containment test
    - **Correlation** — Pearson r heatmap
    - **Clustering** — ML model config & scatter plot
    """)

with col3:
    st.markdown("""
    **3. AI Insights**

    Every analytical view has an AI button.

    - Column descriptions via Cortex
    - Correlation pattern explanation
    - Cluster narrative summaries

    Set your preferred Cortex model in
    **Configuration**.
    """)

st.divider()

# ── Feature reference ──────────────────────────────────────────────────────────

st.subheader("Feature Reference")

tabs = st.tabs(["Profiling", "Relationships", "Correlation", "Clustering", "AI Insights"])

with tabs[0]:
    st.markdown("""
    ### Data Profiling

    For every column DataLens computes:

    | Category | Metrics |
    |---|---|
    | All types | Row count, null count, null rate, distinct count, uniqueness rate |
    | Numeric | min, max, mean, median, stddev, variance, Q1, Q3, skewness, kurtosis |
    | String | min/max/avg character length |
    | Date | earliest and latest date |

    **Alerts** are raised when:
    - Null rate > 50% (Critical) or > configured threshold (Warning)
    - Row count drifts more than the configured threshold vs. previous run
    - Std dev or variance drifts more than the threshold

    Results are stored in `DATALENS.METADATA.PROFILE_RESULTS` as JSON,
    so subsequent page loads are instant — no re-profiling needed.
    """)

with tabs[1]:
    st.markdown("""
    ### Relationship Detection

    DataLens uses the SQL **EXCEPT** set operation to test referential integrity
    between same-named columns across profiled tables.

    **Algorithm:**
    ```
    orphans = DISTINCT(source.col) EXCEPT DISTINCT(target.col)
    match%  = (src_distinct − orphans) / src_distinct
    ```

    | Status | Condition |
    |---|---|
    | 🟢 PASS | 0 orphans — perfect FK-like containment |
    | 🟡 WARN | ≥ 90% match — mostly valid, minor quality issue |
    | 🔴 FAIL | < 90% match — not a valid FK relationship |

    You can test against all profiled tables or select specific ones on the
    **Relationships** page.
    """)

with tabs[2]:
    st.markdown("""
    ### Correlation Matrix

    Computes Pearson correlation coefficients for all numeric column pairs
    in a single Snowflake query using the `CORR()` aggregate function.

    Values range from **−1** (perfect negative correlation) to **+1**
    (perfect positive correlation). **0** means no linear relationship.

    The heatmap uses a **RdBu** diverging colour scale:
    - 🔵 Blue → positive correlation
    - 🔴 Red → negative correlation
    - ⚪ White → no correlation

    **AI Explanation** (Cortex): highlights the strongest and weakest
    correlations and suggests business-level interpretations.
    """)

with tabs[3]:
    st.markdown("""
    ### ML Clustering

    DataLens fetches a configurable sample (default 10,000 rows) and
    applies scikit-learn clustering. Five models are available:

    | Model | Best for |
    |---|---|
    | K-Means | Spherical, equal-size clusters. Fast and interpretable. |
    | Bisecting K-Means | More balanced partition than standard K-Means. |
    | DBSCAN | Arbitrary-shaped clusters; identifies outliers as noise. |
    | Agglomerative | Hierarchical; good for nested structures. |
    | Gaussian Mixture | Probabilistic; handles elliptical clusters. |

    **Feature Engineering** (automatic):
    - `log1p` transform applied to right-skewed columns (skewness > 1)
    - Ratio features suggested for semantically paired columns (price/cost, etc.)

    **Quality Metrics**: Silhouette score, Davies-Bouldin index,
    Calinski-Harabász score.

    Results include a **PCA 2-D scatter plot** for visual exploration.
    """)

with tabs[4]:
    st.markdown("""
    ### AI Insights — Snowflake Cortex

    All AI features use **SNOWFLAKE.CORTEX.COMPLETE()** — no external
    API keys or services required.

    | Feature | What it generates |
    |---|---|
    | Table description | 1-2 sentence summary of the table's purpose |
    | Column descriptions | Business-oriented per-column descriptions |
    | Apply to Snowflake | Runs `ALTER TABLE … COMMENT` to persist descriptions |
    | Correlation insights | Highlights strong patterns and potential data issues |
    | Cluster insights | Names and describes each segment in business language |

    **Fallback**: if Cortex is unavailable, rule-based descriptions are
    generated from column names and data types.

    **Recommended models**: `mistral-large` (best quality),
    `llama3.1-8b` (fastest), `snowflake-arctic` (cost-efficient).
    """)

st.divider()

# ── Architecture ──────────────────────────────────────────────────────────────

with st.expander("Architecture & Design Principles"):
    st.markdown("""
    **No data transfer** — all statistical SQL runs inside Snowflake.
    Only scalar aggregates (counts, means, min/max) reach the app.

    **Persistence-first** — every result is written to `DATALENS.METADATA`
    immediately after completion. Pages reload cached state instantly.

    **Platform abstraction** — `BasePlatform` / `SnowparkPlatform` decouples
    the profiling engine from the Snowflake connection. The same engine drives
    profiling, clustering, relationship detection, and correlation.

    **Graceful failures** — a single bad column is isolated to its own
    `error` field; profiling of all other columns continues.

    **No background threads** — Streamlit in Snowflake runs in a managed
    environment. All operations are synchronous with spinner feedback.
    """)

# ── FAQ ────────────────────────────────────────────────────────────────────────

st.subheader("FAQ")

with st.expander("How long does profiling take?"):
    st.markdown(
        "For a typical 1M-row table with 20 columns, profiling takes 15–60 seconds "
        "depending on your Snowflake warehouse size. Large tables (100M+ rows) may take "
        "several minutes. Results are cached so subsequent views are instant."
    )

with st.expander("Why does clustering sample only 10,000 rows?"):
    st.markdown(
        "ML algorithms (scikit-learn) run in the Streamlit app container, not in Snowflake. "
        "Fetching too many rows would slow down the UI. Increase the sample size slider for "
        "higher-quality clusters on small-to-medium tables."
    )

with st.expander("Can I apply column comments back to the source table?"):
    st.markdown(
        "Yes. In **Report → Columns → Generate Column Descriptions**, click "
        "**Apply All to Snowflake** after reviewing the suggestions. This runs "
        "`ALTER TABLE … ALTER COLUMN … COMMENT` for each column. "
        "Your role must have `MODIFY` privilege on the table."
    )

with st.expander("What Cortex models are available?"):
    st.markdown(
        "Availability depends on your Snowflake region. The **Configuration** page "
        "lists the commonly available models. Use the **Test Cortex** button to "
        "verify a model works in your account."
    )

with st.expander("Where is DataLens data stored?"):
    st.markdown(
        "All metadata is stored in `DATALENS.METADATA` (configurable in **Configuration**). "
        "Tables: `PROFILE_RESULTS`, `RELATIONSHIP_RESULTS`, `CORRELATION_RESULTS`, "
        "`CLUSTERING_RESULTS`, `COLUMN_COLORS`. "
        "Run `setup_datalens_metadata.sql` to create the schema."
    )
