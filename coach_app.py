import streamlit as st
import pandas as pd
import paho.mqtt.client as mqtt
import json
import os
import time
import math
import altair as alt

# --- PAGE CONFIG ---
st.set_page_config(page_title="Live GG", layout="wide")

# --- PASSWORD GATE LAYER ---
def check_password():
    """Returns True if the user entered the correct password."""
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    if st.session_state.authenticated:
        return True

    # Render a clean, non-intrusive terminal login box
    st.title("🔒 Live GG - Secure Access")
    user_password = st.text_input("Enter Coach Access Password:", type="password")
    
    if user_password == "214137": 
        st.session_state.authenticated = True
        st.rerun()
    elif user_password:
        st.error("❌ Invalid Password")
        
    return False

# Halt entire script thread execution right here if locked
if not check_password():
    st.stop()


# --- GLOBAL CROSS-SESSION MEMORY CACHE ---
class GlobalAppState:
    """Explicitly bypasses user session isolation to link all connected devices together."""
    def __init__(self):
        self.history_df = pd.DataFrame(columns=["knots", "bpm", "lat", "lon", "sats", "hdg", "timestamp"])
        self.lineups_archive = []
        self.active_lineup_start_time = None
        self.archive_version = 0

@st.cache_resource
def get_global_state():
    return GlobalAppState()

# Initialize the synchronized server-wide memory pool
global_state = get_global_state()

# --- MQTT CONFIGURATION ---
MQTT_BROKER = "0445b00fffc949f59fd08b2d728b1989.s1.eu.hivemq.cloud"
MQTT_PORT = 8883
MQTT_USER = "ILCA_BOAT"
MQTT_PASSWORD = "Nzl214137"
MQTT_TOPIC = "boat/target/telemetry"

# --- GLOBAL SINGLETON NETWORK CONNECTION ---
@st.cache_resource
def initialize_global_mqtt():
    def on_connect_cb(client, userdata, flags, rc, properties=None):
        if rc == 0:
            client.subscribe(MQTT_TOPIC)
    def on_message_cb(client, userdata, msg):
        try:
            with open("live_telemetry.txt", "w") as f:
                f.write(msg.payload.decode())
        except Exception:
            pass

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
    client.tls_set()
    client.on_connect = on_connect_cb
    client.on_message = on_message_cb
    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    client.loop_start() 
    return client

mqtt_engine = initialize_global_mqtt()

# --- USER-SESSION SPECIFIC VIEWER STATE ---
if "selected_lineup_names" not in st.session_state:
    st.session_state.selected_lineup_names = []  

def calculate_cog(lat1, lon1, lat2, lon2):
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    diffLong = math.radians(lon2 - lon1)
    x = math.sin(diffLong) * math.cos(rlat2)
    y = math.cos(rlat1) * math.sin(rlat2) - (math.sin(rlat1) * math.cos(rlat2) * math.cos(diffLong))
    return (math.degrees(math.atan2(x, y)) + 360) % 360

# --- CALLBACK ENGINES LINKED TO GLOBAL CACHE ---
def cb_start_lineup():
    global_state.active_lineup_start_time = time.time()

def cb_end_lineup():
    start_t = global_state.active_lineup_start_time
    end_t = time.time()
    
    full_history = global_state.history_df.copy()
    lineup_slice = full_history[(full_history["timestamp"] >= start_t) & (full_history["timestamp"] <= end_t)].copy()
    
    if len(lineup_slice) > 3:
        lineup_slice["run_seconds"] = [i * 0.2 for i in range(len(lineup_slice))]
        lineup_id = len(global_state.lineups_archive) + 1
        
        archive_payload = {
            "id": f"run_{int(start_t)}_{lineup_id}_v{global_state.archive_version}", 
            "name": f"Line-Up #{lineup_id} ({time.strftime('%H:%M:%S', time.localtime(start_t))})",
            "df": lineup_slice,
            "avg_sog": lineup_slice["knots"].astype(float).mean(),
            "max_sog": lineup_slice["knots"].astype(float).max(),
            "avg_hr": lineup_slice["bpm"].astype(float).mean()
        }
        global_state.lineups_archive.append(archive_payload)
        
    global_state.active_lineup_start_time = None

def cb_return_live():
    st.session_state.selected_lineup_names = []

def cb_clear_all_archives():
    global_state.lineups_archive = []
    global_state.active_lineup_start_time = None
    st.session_state.selected_lineup_names = []
    global_state.archive_version += 1 
    st.rerun()

# --- SIDEBAR CONTROL PANEL ---
st.sidebar.header("Chart Visibility")
show_speed = st.sidebar.checkbox("SOG", value=True)
show_hr = st.sidebar.checkbox("HR", value=True)
show_cog = st.sidebar.checkbox("COG", value=True)

st.sidebar.markdown("---")
st.sidebar.header("Time Window Config")
time_window = st.sidebar.selectbox("Select History Scale", options=["20s", "1m", "2m", "3m", "5m"], index=1)

window_map = {"20s": 20 * 5, "1m": 60 * 5, "2m": 120 * 5, "3m": 180 * 5, "5m": 300 * 5}
display_records = window_map[time_window]

# --- REFRESH INGESTION PIPELINE ---
current_packet = None
if os.path.exists("live_telemetry.txt"):
    try:
        with open("live_telemetry.txt", "r") as f:
            raw_string = f.read()
        current_packet = json.loads(raw_string)
        
        if "last_processed_packet" not in st.session_state or st.session_state.last_processed_packet != raw_string:
            st.session_state.last_processed_packet = raw_string
            df_hist = global_state.history_df
            calculated_hdg = 0.0
            
            if not df_hist.empty:
                last_entry = df_hist.iloc[-1]
                calculated_hdg = last_entry.get("hdg", 0.0)
                
                if current_packet.get("knots", 0.0) > 0.4:
                    lat1, lon1 = float(last_entry["lat"]), float(last_entry["lon"])
                    lat2, lon2 = float(current_packet["lat"]), float(current_packet["lon"])
                    if lat1 != lat2 or lon1 != lon2:
                        calculated_hdg = calculate_cog(lat1, lon1, lat2, lon2)
            
            current_packet["hdg"] = calculated_hdg
            current_packet["timestamp"] = time.time()
            
            new_row = pd.DataFrame([current_packet])
            global_state.history_df = pd.concat([df_hist, new_row], ignore_index=True).iloc[-4000:] 
    except Exception:
        pass

# --- SIDEBAR MULTI-COMPARE INTERFACE CONTAINER ---
sidebar_container = st.sidebar.empty()

if len(global_state.lineups_archive) > 0:
    with sidebar_container.container():
        st.markdown("---")
        st.header("📦 Compare Line-Ups")
        
        available_names = [item["name"] for item in global_state.lineups_archive]
        
        st.session_state.selected_lineup_names = st.multiselect(
            "Select traces to overlay:",
            options=available_names,
            default=[name for name in st.session_state.selected_lineup_names if name in available_names],
            key=f"compare_select_v{global_state.archive_version}"
        )
                
        st.markdown("---")
        st.button(
            "🗑️ CLEAR ALL ARCHIVES", 
            key=f"clear_btn_v{global_state.archive_version}", 
            type="secondary", 
            on_click=cb_clear_all_archives, 
            use_container_width=True
        )
else:
    sidebar_container.empty()

# =========================================================
#  MODE A: FULL SCREEN MULTI-LINE OVERLAY COMPARISON VIEW
# =========================================================
if len(st.session_state.selected_lineup_names) > 0:
    target_runs = [item for item in global_state.lineups_archive if item["name"] in st.session_state.selected_lineup_names]
    
    head_col1, head_col2 = st.columns([3, 1])
    with head_col1:
        st.title("📊 Multi-Line Overlay Analysis")
    with head_col2:
        st.button("🔄 RETURN TO LIVE STREAM", type="primary", on_click=cb_return_live, use_container_width=True, key="ret_live_debrief_btn")

    stat_cols = st.columns(len(target_runs))
    for i, run in enumerate(target_runs):
        with stat_cols[i]:
            st.markdown(f"**{run['name']}**")
            st.markdown(f"Avg Speed: `{run['avg_sog']:.2f} kts` | Peak: `{run['max_sog']:.2f} kts` | Exertion: `{run['avg_hr']:.0f} BPM`")
    st.markdown("---")

    combined_list = []
    for run in target_runs:
        df_temp = run["df"].copy()
        df_temp["LineUp"] = run["name"]
        
        df_temp["knots_smooth"] = df_temp["knots"].astype(float).rolling(window=10, min_periods=1).mean()
        df_temp["bpm_smooth"] = df_temp["bpm"].astype(float).rolling(window=10, min_periods=1).mean()
        df_temp["hdg_smooth"] = df_temp["hdg"].astype(float).rolling(window=10, min_periods=1).mean()
        
        combined_list.append(df_temp)
    
    df_compare = pd.concat(combined_list, ignore_index=True)

    if show_speed:
        st.markdown("### Speed Comparison Overlay (SOG)")
        sog_chart = alt.Chart(df_compare).mark_line(strokeWidth=3).encode(
            x=alt.X('run_seconds:Q', title="Elapsed Duration (Seconds)"),
            y=alt.Y('knots_smooth:Q', title="Knots (Smoothed)", scale=alt.Scale(zero=False)),
            color=alt.Color('LineUp:N', title="Traces", scale=alt.Scale(scheme='category10'))
        ).properties(width='container', height=300).interactive()
        st.altair_chart(sog_chart, use_container_width=True)
