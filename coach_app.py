import streamlit as st
import paho.mqtt.client as mqtt
import json
import time
import pandas as pd
from collections import deque
from datetime import datetime

# ── MQTT Config ──────────────────────────────────────────
MQTT_BROKER = "145.241.230.146"   # Oracle Cloud VM
MQTT_PORT   = 9001                 # WebSockets port
MQTT_TOPIC  = "boat/target/telemetry"

# ── State ────────────────────────────────────────────────
MAX_POINTS = 300  # 60 seconds at 5Hz

if "telemetry" not in st.session_state:
    st.session_state.telemetry = deque(maxlen=MAX_POINTS)
if "connected" not in st.session_state:
    st.session_state.connected = False
if "last_msg" not in st.session_state:
    st.session_state.last_msg = None

# ── MQTT Callbacks ────────────────────────────────────────
def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        st.session_state.connected = True
        client.subscribe(MQTT_TOPIC)
        print(f"[MQTT] Connected and subscribed to {MQTT_TOPIC}")
    else:
        print(f"[MQTT] Connection failed rc={rc}")

def on_disconnect(client, userdata, rc, properties=None, reasonCode=None):
    st.session_state.connected = False
    print("[MQTT] Disconnected")

def on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload.decode())
        data["time"] = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        st.session_state.telemetry.append(data)
        st.session_state.last_msg = data
    except Exception as e:
        print(f"[MQTT] Parse error: {e}")

# ── MQTT Client (singleton per session) ──────────────────
@st.cache_resource
def get_mqtt_client():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, transport="websockets")
    client.on_connect    = on_connect
    client.on_disconnect = on_disconnect
    client.on_message    = on_message
    client.connect(MQTT_BROKER, MQTT_PORT, 60)
    client.loop_start()
    return client

client = get_mqtt_client()

# ── UI ────────────────────────────────────────────────────
st.set_page_config(page_title="Sailing Coach", layout="wide", page_icon="⛵")
st.title("⛵ Sailing Coach Telemetry")

status_col, refresh_col = st.columns([3, 1])
with status_col:
    if st.session_state.connected:
        st.success(f"🟢 Connected to {MQTT_BROKER}:{MQTT_PORT}")
    else:
        st.error("🔴 Disconnected from broker")

with refresh_col:
    if st.button("🔄 Refresh"):
        st.rerun()

# ── Latest Values ─────────────────────────────────────────
if st.session_state.last_msg:
    d = st.session_state.last_msg
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🚤 Speed (knots)", f"{d.get('knots', 0):.2f}")
    c2.metric("❤️ Heart Rate",    f"{d.get('bpm', 0)} bpm")
    c3.metric("🛰️ Satellites",    d.get('sats', 0))
    c4.metric("📍 Position",      f"{d.get('lat', 0):.5f}, {d.get('lon', 0):.5f}")
else:
    st.info("⏳ Waiting for data from boat...")

# ── Charts ────────────────────────────────────────────────
if st.session_state.telemetry:
    df = pd.DataFrame(list(st.session_state.telemetry))

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Speed (knots)")
        st.line_chart(df.set_index("time")["knots"] if "time" in df else df["knots"])

    with col2:
        st.subheader("Heart Rate (bpm)")
        if "bpm" in df.columns:
            st.line_chart(df.set_index("time")["bpm"] if "time" in df else df["bpm"])

# ── Raw Data ──────────────────────────────────────────────
with st.expander("Raw telemetry (last 20 messages)"):
    if st.session_state.telemetry:
        recent = list(st.session_state.telemetry)[-20:]
        st.dataframe(pd.DataFrame(recent), use_container_width=True)

# ── Auto-refresh every 200ms ──────────────────────────────
time.sleep(0.2)
st.rerun()
