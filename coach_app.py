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

    if show_hr:
        st.markdown("### Physical Exertion Overlay (HR)")
        hr_chart = alt.Chart(df_compare).mark_line(strokeWidth=3).encode(
            x=alt.X('run_seconds:Q', title="Elapsed Duration (Seconds)"),
            y=alt.Y('bpm_smooth:Q', title="Heart Rate (BPM)", scale=alt.Scale(zero=False)),
            color=alt.Color('LineUp:N', title="Traces", scale=alt.Scale(scheme='category10'))
        ).properties(width='container', height=250).interactive()
        st.altair_chart(hr_chart, use_container_width=True)

    if show_cog:
        st.markdown("### Course Over Ground Overlay (COG)")
        cog_chart = alt.Chart(df_compare).mark_line(strokeWidth=3).encode(
            x=alt.X('run_seconds:Q', title="Elapsed Duration (Seconds)"),
            y=alt.Y('hdg_smooth:Q', title="Heading Degrees (°)", scale=alt.Scale(zero=False)),
            color=alt.Color('LineUp:N', title="Traces", scale=alt.Scale(scheme='category10'))
        ).properties(width='container', height=250).interactive()
        st.altair_chart(cog_chart, use_container_width=True)

# =========================================================
#  MODE B: REAL-TIME 5Hz PERFORMANCE STREAM INTERFACE
# =========================================================
else:
    st.title("Live GG")
    st.markdown("### Line Up Recorder")
    ctrl_col1, ctrl_col2, ctrl_col3 = st.columns([1, 1, 2])

    if global_state.active_lineup_start_time is None:
        st.button("🟢 START LINE-UP", on_click=cb_start_lineup, use_container_width=True, key=f"start_btn_v{global_state.archive_version}")
    else:
        st.button("🔴 END LINE-UP", on_click=cb_end_lineup, use_container_width=True, key=f"end_btn_v{global_state.archive_version}")

        elapsed = time.time() - global_state.active_lineup_start_time
        ctrl_col3.markdown(f"<div style='background-color:#1E293B; padding:10px; border-radius:5px; border-left: 4px solid #00FF00; color:#FFFFFF;'>⏱️ <b>Line-up Active:</b> {elapsed:.1f}s elapsed</div>", unsafe_allow_html=True)

    st.markdown("---")

    if current_packet is None or global_state.history_df.empty:
        st.info("Awaiting live stream telemetry array...")
    else:
        filtered_df = global_state.history_df.tail(display_records).copy()
        total_rows = len(filtered_df)
        filtered_df["seconds_ago"] = [(-total_rows + 1 + i) * 0.2 for i in range(total_rows)]
        
        latest_record = filtered_df.iloc[-1]

        def build_sog_chart(df):
            series_raw = df["knots"].astype(float)
            window_avg = float(series_raw.mean())
            window_min = float(series_raw.min())
            window_max = float(series_raw.max())
            
            df_smooth = df.copy()
            df_smooth["knots"] = df_smooth["knots"].rolling(window=10, min_periods=1).mean()

            max_deviation = max(abs(window_max - window_avg), abs(window_avg - window_min))
            padding = max_deviation * 0.10 if max_deviation > 0 else 0.5
            y_scale_min = max(0.0, window_avg - max_deviation - padding)
            y_scale_max = window_avg + max_deviation + padding

            df_segments = df_smooth.copy()
            df_segments['next_seconds_ago'] = df_segments['seconds_ago'].shift(-1)
            df_segments['next_knots'] = df_segments['knots'].shift(-1)
            df_segments = df_segments.dropna(subset=['next_seconds_ago', 'next_knots'])
            df_segments['segment_avg'] = (df_segments['knots'] + df_segments['next_knots']) / 2.0

            chart = alt.Chart(df_segments).mark_line(clip=True, strokeWidth=4).encode(
                x=alt.X('seconds_ago:Q', title=None, axis=alt.Axis(labels=False, ticks=False)),
                x2='next_seconds_ago:Q',
                y=alt.Y('knots:Q', title=None, scale=alt.Scale(domain=[y_scale_min, y_scale_max]),
                        axis=alt.Axis(values=[window_min, window_max], format=".2f", labelFontSize=18, labelFontWeight="bold")),
                y2='next_knots:Q',
                color=alt.condition(alt.datum.segment_avg >= window_avg, alt.value("#00FF00"), alt.value("#FF0000"))
            ).properties(width='container', height=200)
            return chart

        def build_standard_chart(df, y_column, hex_color, is_integer=False):
            series_raw = df[y_column].astype(float)
            window_min = float(series_raw.min())
            window_max = float(series_raw.max())
            
            df_smooth = df.copy()
            df_smooth[y_column] = df_smooth[y_column].rolling(window=10, min_periods=1).mean()

            delta = window_max - window_min
            padding = delta * 0.08 if delta > 0 else 1.0
            y_scale_min = max(0.0, window_min - padding)
            y_scale_max = window_max + padding

            chart = alt.Chart(df_smooth).mark_line(clip=True, strokeWidth=3).encode(
                x=alt.X('seconds_ago:Q', title=None, axis=alt.Axis(labels=False, ticks=False)),
                y=alt.Y(f'{y_column}:Q', title=None, scale=alt.Scale(domain=[y_scale_min, y_scale_max]),
                        axis=alt.Axis(values=[window_min, window_max], format=".2f" if not is_integer else ".0f", labelFontSize=18, labelFontWeight="bold")),
                color=alt.value(hex_color)
            ).properties(width='container', height=200)
            return chart

        if show_speed:
            c1, c2 = st.columns([1, 4], gap="medium")
            with c1:
                live_sog = float(latest_record['knots'])
                st.markdown("<div style='padding-top: 55px; text-align: left;'>", unsafe_allow_html=True)
                st.markdown(f"<h1 style='font-size: 64px; color: #00FF00; margin: 0px; font-weight: bold;'>{live_sog:.2f} <span style='font-size: 24px;'>kts</span></h1>", unsafe_allow_html=True)
                st.markdown("</div>", unsafe_allow_html=True)
            with c2:
                st.altair_chart(build_sog_chart(filtered_df), use_container_width=True)
            st.markdown("<hr style='margin-top:10px; margin-bottom:10px;'/>", unsafe_allow_html=True)

        if show_hr:
            c1, c2 = st.columns([1, 4], gap="medium")
            with c1:
                live_hr = int(latest_record['bpm'])
                hr_text = f"{live_hr}" if live_hr > 0 else "STBY"
                st.markdown("<div style='padding-top: 55px; text-align: left;'>", unsafe_allow_html=True)
                st.markdown(f"<h1 style='font-size: 64px; color: #FF0000; margin: 0px; font-weight: bold;'>{hr_text} <span style='font-size: 24px;'>BPM</span></h1>", unsafe_allow_html=True)
                st.markdown("</div>", unsafe_allow_html=True)
            with c2:
                st.altair_chart(build_standard_chart(filtered_df, "bpm", "#FF0000", is_integer=True), use_container_width=True)
            st.markdown("<hr style='margin-top:10px; margin-bottom:10px;'/>", unsafe_allow_html=True)

        if show_cog:
            c1, c2 = st.columns([1, 4], gap="medium")
            with c1:
                live_cog = float(latest_record['hdg'])
                st.markdown("<div style='padding-top: 55px; text-align: left;'>", unsafe_allow_html=True)
                st.markdown(f"<h1 style='font-size: 64px; color: #000000; margin: 0px; font-weight: bold;'>{live_cog:.0f}°</h1>", unsafe_allow_html=True)
                st.markdown("</div>", unsafe_allow_html=True)
            with c2:
                st.altair_chart(build_standard_chart(filtered_df, "hdg", "#000000", is_integer=True), use_container_width=True)

# 5Hz refresh rate execution lock
time.sleep(0.2)
st.rerun()