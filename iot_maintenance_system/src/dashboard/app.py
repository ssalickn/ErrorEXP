"""
Streamlit Dashboard for IoT Predictive Maintenance
Real-time monitoring, alerts, and SHAP explanations
"""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, timedelta
import joblib
from pathlib import Path
from typing import Dict, List

# Page config
st.set_page_config(
    page_title="IoT Maintenance Monitor",
    page_icon="chart_with_upwards_trend",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS styling
st.markdown("""
<style>
    .metric-card {
        background-color: #f0f2f6;
        border-radius: 8px;
        padding: 16px;
        margin: 8px 0;
    }
    .critical-alert {
        background-color: #ffcccc;
        border-left: 4px solid #cc0000;
    }
    .high-alert {
        background-color: #ffddaa;
        border-left: 4px solid #ff9900;
    }
    .medium-alert {
        background-color: #ffffcc;
        border-left: 4px solid #ffff00;
    }
</style>
""", unsafe_allow_html=True)

# ==================== PATH CONFIGURATION ====================
# Dynamically resolve root directories relative to this file's path
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent  # Points safely to iot_maintenance_system/

DATA_DIR = PROJECT_ROOT / "data"
MODEL_DIR = PROJECT_ROOT / "models"

# ==================== SESSION STATE & CACHING ====================

@st.cache_resource
def load_models():
    """Load trained models with absolute path tracking"""
    try:
        scaler = joblib.load(MODEL_DIR / 'scaler_v1.pkl')
        survival = joblib.load(MODEL_DIR / 'weibull_aft_v1.pkl')
        classification = joblib.load(MODEL_DIR / 'xgboost_rca_v1.pkl')
        feature_names = joblib.load(MODEL_DIR / 'feature_names_v1.pkl')
        return {
            'scaler': scaler,
            'survival': survival,
            'classification': classification,
            'feature_names': feature_names
        }
    except Exception as e:
        st.sidebar.error(f"Failed to load models: {str(e)}")
        return None

@st.cache_data
def load_telemetry_data():
    """Load telemetry data"""
    try:
        return pd.read_parquet(DATA_DIR / 'raw_telemetry.parquet')
    except Exception as e:
        return None

@st.cache_data
def load_features_data():
    """Load engineered features"""
    try:
        return pd.read_parquet(DATA_DIR / 'processed_features.parquet')
    except Exception as e:
        return None

@st.cache_data
def load_labels_data():
    """Load failure labels"""
    try:
        return pd.read_parquet(DATA_DIR / 'labels.parquet')
    except Exception as e:
        return None

# ==================== MAIN DASHBOARD ====================

def main():
    # Header
    st.markdown("# Industrial IoT Predictive Maintenance Dashboard")
    st.markdown("Real-time failure prediction and root cause analysis for network infrastructure devices")
    
    # Load data dependencies
    models = load_models()
    telemetry = load_telemetry_data()
    features = load_features_data()
    labels = load_labels_data()
    
    # Check if all data was loaded successfully
    data_missing = (
        models is None or 
        telemetry is None or 
        features is None or 
        labels is None or
        telemetry.empty or
        features.empty or
        labels.empty
    )
    
    if data_missing:
        st.error("Required data not found. Please verify paths or run the full pipeline first:")
        st.code("""
python src/data_pipeline/data_generator.py
python src/data_pipeline/feature_engineer.py
python src/models/model_trainer.py
        """)
        return
    
    # Sidebar navigation
    st.sidebar.title("Navigation")
    page = st.sidebar.radio(
        "Select View",
        ["Dashboard", "Alerts", "Device Details", "Analytics", "Model Information"]
    )
    
    # Render selected page view context
    if page == "Dashboard":
        show_dashboard(features, labels, models)
    elif page == "Alerts":
        show_alerts(features, models)
    elif page == "Device Details":
        show_device_details(telemetry, features, labels, models)
    elif page == "Analytics":
        show_analytics(labels, features)
    elif page == "Model Information":
        show_model_info(models, features)


def show_dashboard(features: pd.DataFrame, labels: pd.DataFrame, models: Dict):
    """Main overview panel"""
    st.header("System Overview")
    
    # Key metrics calculation layout
    col1, col2, col3, col4 = st.columns(4)
    total_devices = len(features)
    failed_devices = len(labels) if labels is not None else 0
    failure_rate = (failed_devices / total_devices * 100) if total_devices > 0 else 0
    
    with col1:
        st.metric("Total Active Devices", total_devices, delta=None)
    with col2:
        st.metric("Logged Failed Devices", failed_devices, f"{failure_rate:.1f}% Risk Rate")
    with col3:
        avg_ttf = features['ttf_days'].mean() if 'ttf_days' in features else 0
        st.metric("Avg Expected TTF", f"{avg_ttf:.0f} Days")
    with col4:
        recent_failures = len(labels[
            labels['failure_timestamp'] > (datetime.now() - timedelta(days=30))
        ]) if (labels is not None and not labels.empty) else 0
        st.metric("Failures (Last 30d)", recent_failures)
    
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Device Distribution by Type")
        device_counts = features['device_type'].value_counts()
        fig = px.pie(names=device_counts.index, values=device_counts.values, hole=0.4)
        st.plotly_chart(fig, use_container_width=True)
    
    with col2:
        st.subheader("Failures by Specific Mode")
        if labels is not None and not labels.empty and 'failure_mode' in labels.columns:
            failure_modes = labels['failure_mode'].value_counts()
            fig = px.pie(names=failure_modes.index, values=failure_modes.values, hole=0.4)
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No categorical breakdown logs available.")
            
    # Key Risk Indicator metrics
    st.subheader("Top System Risk Factors")
    risk_cols = ['voltage_volatility', 'packet_loss_trend', 'memory_pressure', 'device_age_days']
    valid_risk_cols = [c for c in risk_cols if c in features.columns]
    
    if valid_risk_cols:
        mean_risks = features[valid_risk_cols].mean().reset_index()
        mean_risks.columns = ['Risk Metric', 'Mean Intensity Value']
        fig = px.bar(mean_risks, x='Mean Intensity Value', y='Risk Metric', orientation='h', color='Mean Intensity Value')
        st.plotly_chart(fig, use_container_width=True)


def show_alerts(features: pd.DataFrame, models: Dict):
    """Alert review module"""
    st.header("Device Alerts & Risk Assessment")
    
    # Secure feature references dynamically - focus on infrastructure metrics only
    risk_score = np.zeros(len(features))
    if 'voltage_volatility' in features.columns: risk_score += (features['voltage_volatility'] / 10.0).clip(0, 1) * 0.25
    if 'packet_loss_trend' in features.columns: risk_score += (features['packet_loss_trend'] / 0.5).clip(0, 1) * 0.20
    if 'memory_pressure' in features.columns: risk_score += features['memory_pressure'] * 0.20
    if 'device_age_days' in features.columns: risk_score += (features['device_age_days'] / 1460.0).clip(0, 1) * 0.35
    
    features['risk_score'] = risk_score
    features['risk_level'] = pd.cut(
        features['risk_score'],
        bins=[-0.01, 0.25, 0.50, 0.70, 1.01],
        labels=['Low', 'Medium', 'High', 'Critical']
    )
    
    # Initialize session state for alert filter
    if 'alert_filter' not in st.session_state:
        st.session_state.alert_filter = 'Critical'
    
    # Filter buttons
    st.markdown("### Filter by Risk Level")
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        if st.button("Critical", use_container_width=True):
            st.session_state.alert_filter = 'Critical'
    with col2:
        if st.button("High", use_container_width=True):
            st.session_state.alert_filter = 'High'
    with col3:
        if st.button("Medium", use_container_width=True):
            st.session_state.alert_filter = 'Medium'
    with col4:
        if st.button("Low", use_container_width=True):
            st.session_state.alert_filter = 'Low'
    
    risk_filter = st.session_state.alert_filter
    alerts_filtered = features[features['risk_level'] == risk_filter].sort_values('risk_score', ascending=False)
    
    if alerts_filtered.empty:
        st.info(f"No devices detected with {risk_filter.lower()} risk status.")
        return
    
    st.markdown(f"### {risk_filter} Priority Alerts")
    st.markdown(f"**Total: {len(alerts_filtered)} device(s)**")
    
    # Create table display
    display_data = []
    for _, device in alerts_filtered.iterrows():
        display_data.append({
            'Device ID': device['device_id'],
            'Device Type': device['device_type'],
            'Location': device['zone'],
            'Risk Score': f"{device['risk_score']:.2f}",
            'Est. Days to Failure': f"{device.get('ttf_days', 'N/A'):.1f}" if isinstance(device.get('ttf_days'), (int, float)) else "N/A"
        })
    
    alert_df = pd.DataFrame(display_data)
    st.dataframe(alert_df, use_container_width=True, hide_index=True)


def show_device_details(telemetry: pd.DataFrame, features: pd.DataFrame, labels: pd.DataFrame, models: Dict):
    """Device tracking sub-module"""
    st.header("Device Telemetry Analysis")
    
    selected_id = st.selectbox("Isolate Active Hardware Identifier", sorted(features['device_id'].unique()))
    if not selected_id: return
    
    dev_features = features[features['device_id'] == selected_id].iloc[0]
    dev_telemetry = telemetry[telemetry['device_id'] == selected_id].sort_values('timestamp') if telemetry is not None else pd.DataFrame()
    
    c1, c2, c3 = st.columns(3)
    c1.metric("Asset Classification", str(dev_features['device_type']))
    c2.metric("Operational Zone", str(dev_features['zone']))
    c3.metric("Running Service Lifespan", f"{(dev_features['device_age_days']/365.0):.2f} Years")
    
    if not dev_telemetry.empty:
        st.subheader("Telemetry Metrics Trends")
        metric_choices = [c for c in ['voltage', 'cpu_utilization', 'packet_loss', 'bandwidth_util', 'ping_latency'] if c in dev_telemetry.columns]
        chosen_metric = st.selectbox("Select Metric", metric_choices)
        
        if chosen_metric:
            fig = px.line(dev_telemetry, x='timestamp', y=chosen_metric, title=f"{chosen_metric.title()} Over Time")
            st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No telemetry data available for this device.")


def show_analytics(labels: pd.DataFrame, features: pd.DataFrame):
    """Aggregated Analytics"""
    st.header("System Failure Analytics")
    if labels is None or labels.empty:
        st.info("No failure data available.")
        return
        
    c1, c2 = st.columns(2)
    with c1:
        if 'device_type' in labels.columns:
            counts = labels['device_type'].value_counts().reset_index()
            st.plotly_chart(px.bar(counts, x='device_type', y='count', title="Failure Incidents across Profile types"), use_container_width=True)
    with c2:
        if 'zone' in labels.columns:
            counts = labels['zone'].value_counts().reset_index()
            st.plotly_chart(px.bar(counts, x='zone', y='count', title="Failure Geolocation Distributions"), use_container_width=True)


def show_model_info(models: Dict, features: pd.DataFrame):
    """Model framework metadata registry review"""
    st.header("Model Information")
    
    st.markdown("""
    ### System Pipeline Architecture
    * **Stage 1 (Proactive Lifespan Prediction):** Parametric Weibull Accelerated Failure Time (AFT) Regression Engine.
    * **Stage 2 (Root-Cause Diagnosis Classification):** Multi-Class Tree Gradient-Boosted Classifier Framework (`XGBoost`).
    * **Model Explainability Engine:** Algorithmic SHAP (SHapley Additive exPlanations) Vector Interrogations.
    """)
    
    st.subheader("Model Input Features Mapping Matrix")
    st.dataframe(pd.DataFrame({'Registered Feature Name': models['feature_names']}), use_container_width=True)


if __name__ == "__main__":
    main()