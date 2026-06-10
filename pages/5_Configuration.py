"""DataLens -- Configuration page. AI provider, metadata schema, connection info."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st


from sis_session import get_session
try:
    session = get_session()
except RuntimeError as _e:
    st.error(str(_e))
    st.stop()

import sis_persistence as persistence
import sis_cortex as cortex
from platforms.snowpark_platform import SnowparkPlatform

platform = SnowparkPlatform(session)

st.title("Configuration")

# ── Connection info ───────────────────────────────────────────────────────────
st.subheader("Snowflake Connection")
info = platform.test_connection()
if info.get("ok"):
    col1, col2, col3 = st.columns(3)
    col1.metric("User",      info.get("user", "--"))
    col2.metric("Warehouse", info.get("warehouse", "--"))
    col3.metric("Database",  info.get("database", "--"))
    st.success("Connected to Snowflake")
else:
    st.error(f"Connection error: {info.get('error', 'Unknown')}")

# ── AI Provider ───────────────────────────────────────────────────────────────
st.divider()
st.subheader("AI Insights Provider")
st.caption(
    "Choose between **Snowflake Cortex** (runs inside Snowflake, no external calls) "
    "and **Ollama** (local LLM server, best for local development)."
)

provider_options = {
    "Cortex (Snowflake)": cortex.PROVIDER_CORTEX,
    "Ollama (Local)":     cortex.PROVIDER_OLLAMA,
}
current_provider = st.session_state.get("ai_provider", cortex.PROVIDER_CORTEX)
provider_label   = next(
    (k for k, v in provider_options.items() if v == current_provider),
    "Cortex (Snowflake)",
)

chosen_provider_label = st.radio(
    "Provider",
    list(provider_options.keys()),
    index=list(provider_options.keys()).index(provider_label),
    horizontal=True,
    key="cfg_provider_radio",
)
chosen_provider = provider_options[chosen_provider_label]

if st.button("Apply Provider", key="cfg_apply_provider"):
    st.session_state["ai_provider"] = chosen_provider
    try:
        persistence.save_app_setting(session, "ai_provider", chosen_provider)
    except Exception:
        pass
    st.success(f"AI provider set to **{chosen_provider_label}**")

st.divider()

# ── Cortex settings ───────────────────────────────────────────────────────────
if chosen_provider == cortex.PROVIDER_CORTEX:
    st.subheader("Snowflake Cortex Settings")
    st.caption("Uses `SNOWFLAKE.CORTEX.COMPLETE()`. Available in most Snowflake regions.")

    current_model = st.session_state.get("cortex_model", cortex.DEFAULT_MODEL)
    model_idx     = (
        cortex.AVAILABLE_MODELS.index(current_model)
        if current_model in cortex.AVAILABLE_MODELS else 0
    )

    chosen_model = st.selectbox(
        "Cortex Model",
        cortex.AVAILABLE_MODELS,
        index=model_idx,
        key="cfg_cortex_model",
        help="mistral-large offers the best balance of quality and speed.",
    )

    col_save, col_test = st.columns([1, 1])
    with col_save:
        if st.button("Save Model", key="cfg_save_cortex_model"):
            st.session_state["cortex_model"] = chosen_model
            try:
                persistence.save_app_setting(session, "cortex_model", chosen_model)
            except Exception:
                pass
            st.success(f"Cortex model set to **{chosen_model}**")

    with col_test:
        if st.button("Test Cortex", key="cfg_test_cortex"):
            st.session_state["ai_provider"] = cortex.PROVIDER_CORTEX
            with st.spinner("Testing Cortex ..."):
                result = cortex.complete(session, "Reply with exactly: ONLINE", model=chosen_model)
            if result:
                st.success(f"Cortex response: **{result[:100]}**")
            else:
                st.error(
                    "Cortex returned no response. Verify the model name and that "
                    "Cortex is enabled in your Snowflake account."
                )

# ── Ollama settings ───────────────────────────────────────────────────────────
else:
    st.subheader("Ollama Settings")
    st.info(
        "Ollama runs a local LLM server on your machine. "
        "Install from [ollama.ai](https://ollama.ai), then run `ollama serve` "
        "and `ollama pull llama3.2` before using this option. "
        "**Note:** Ollama is not accessible when the app runs inside Snowflake (SiS)."
    )

    saved_url   = st.session_state.get("ollama_url",   cortex.OLLAMA_DEFAULT_URL)
    saved_model = st.session_state.get("ollama_model", cortex.OLLAMA_DEFAULT_MODEL)

    ollama_url = st.text_input(
        "Ollama base URL",
        value=saved_url,
        key="cfg_ollama_url",
        help="Default: http://localhost:11434",
    )

    col_check, col_spacer = st.columns([1, 3])
    with col_check:
        if st.button("Check Connection", key="cfg_ollama_check"):
            if cortex.ollama_is_available(ollama_url):
                st.success("Ollama is reachable")
            else:
                st.error(
                    f"Cannot reach Ollama at {ollama_url}. "
                    "Make sure `ollama serve` is running."
                )

    # Dynamic model list
    st.markdown("**Model**")
    available_models = cortex.ollama_list_models(ollama_url)

    if available_models:
        default_idx = (
            available_models.index(saved_model)
            if saved_model in available_models else 0
        )
        chosen_ollama_model = st.selectbox(
            "Ollama Model",
            available_models,
            index=default_idx,
            key="cfg_ollama_model_select",
            help="Models currently pulled in Ollama.",
        )
    else:
        chosen_ollama_model = st.text_input(
            "Ollama Model (type manually — could not fetch list)",
            value=saved_model,
            key="cfg_ollama_model_text",
        )
        st.caption(
            "Could not fetch model list from Ollama. "
            "Check the URL above or run `ollama pull <model>`."
        )

    col_save2, col_test2 = st.columns([1, 1])
    with col_save2:
        if st.button("Save Ollama Settings", key="cfg_save_ollama"):
            st.session_state["ollama_url"]   = ollama_url
            st.session_state["ollama_model"] = chosen_ollama_model
            try:
                persistence.save_app_setting(session, "ollama_url",   ollama_url)
                persistence.save_app_setting(session, "ollama_model", chosen_ollama_model)
            except Exception:
                pass
            st.success(
                f"Ollama configured: `{chosen_ollama_model}` at `{ollama_url}`"
            )

    with col_test2:
        if st.button("Test Ollama", key="cfg_test_ollama"):
            st.session_state["ai_provider"] = cortex.PROVIDER_OLLAMA
            st.session_state["ollama_url"]   = ollama_url
            st.session_state["ollama_model"] = chosen_ollama_model
            with st.spinner(f"Asking {chosen_ollama_model} ..."):
                result = cortex.complete(
                    session,
                    "Reply with exactly: ONLINE",
                    model=chosen_ollama_model,
                )
            if result:
                st.success(f"Ollama response: **{result[:100]}**")
            else:
                st.error(
                    "No response from Ollama. Check the URL, model name, "
                    "and that `ollama serve` is running."
                )

# ── Metadata schema ───────────────────────────────────────────────────────────
st.divider()
st.subheader("Metadata Storage")
st.caption(
    "DataLens stores profiling results, correlations, clustering and relationships "
    "in a dedicated Snowflake schema."
)

meta_db = st.text_input(
    "Metadata Database",
    value=st.session_state.get("meta_db", persistence.DEFAULT_DB),
    key="cfg_meta_db",
)
meta_sc = st.text_input(
    "Metadata Schema",
    value=st.session_state.get("meta_schema", persistence.DEFAULT_SCHEMA),
    key="cfg_meta_schema",
)

col_apply, col_reinit = st.columns([1, 1])
with col_apply:
    if st.button("Apply Metadata Location", key="cfg_apply_meta"):
        st.session_state["meta_db"]     = meta_db.upper()
        st.session_state["meta_schema"] = meta_sc.upper()
        st.session_state.pop("meta_initialized", None)
        try:
            persistence.save_app_setting(session, "meta_db",     meta_db.upper())
            persistence.save_app_setting(session, "meta_schema", meta_sc.upper())
        except Exception:
            pass
        st.success(
            f"Metadata will use **{meta_db.upper()}.{meta_sc.upper()}**. "
            "Refresh the Dashboard to reinitialize."
        )

with col_reinit:
    if st.button("Reinitialize Metadata Tables", key="cfg_reinit"):
        with st.spinner("Creating tables ..."):
            try:
                persistence.initialize(session)
                st.session_state["meta_initialized"] = True
                st.success("Metadata tables verified / created.")
            except Exception as e:
                st.error(f"Init failed: {e}")

# ── Sample data ───────────────────────────────────────────────────────────────
st.divider()
st.subheader("Sample Data")
st.caption(
    "Run the SQL scripts below in a Snowflake worksheet to create sample schemas "
    "for exploring DataLens features."
)

col_retail, col_organic = st.columns(2)
with col_retail:
    st.markdown("**Retail Schema** (`SAMPLE_DW.RETAIL`)")
    st.markdown("Fact + 5 dimension tables, ~10,000 rows")
    st.code("-- Run setup_snowflake.sql in a Snowflake worksheet", language="sql")
with col_organic:
    st.markdown("**Organic Sales** (`SAMPLE_DW.ORGANIC`)")
    st.markdown("12M rows with seasonal distribution")
    st.code("-- Run setup_organic_sales.sql in a Snowflake worksheet", language="sql")

# ── About ─────────────────────────────────────────────────────────────────────
st.divider()
st.subheader("About DataLens")
active_provider = st.session_state.get("ai_provider", cortex.PROVIDER_CORTEX)
if active_provider == cortex.PROVIDER_OLLAMA:
    ai_info = (
        f"Ollama / `{st.session_state.get('ollama_model', cortex.OLLAMA_DEFAULT_MODEL)}` "
        f"at `{st.session_state.get('ollama_url', cortex.OLLAMA_DEFAULT_URL)}`"
    )
else:
    ai_info = f"Cortex / `{st.session_state.get('cortex_model', cortex.DEFAULT_MODEL)}`"

st.markdown(f"""
| | |
|---|---|
| **Version** | 2.0 (Streamlit in Snowflake) |
| **AI provider** | {ai_info} |
| **Metadata** | `{st.session_state.get('meta_db', persistence.DEFAULT_DB)}.{st.session_state.get('meta_schema', persistence.DEFAULT_SCHEMA)}` |
""")
