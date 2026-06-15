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
import requests
import sys
from typing import Dict, List

# Page config
st.set_page_config(
    page_title="IoT Maintenance Monitor",
    page_icon="🏭",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS
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

# ==================== SESSION STATE & CACHING ====================

@st.cache_resource
def load_models():
    """Load trained models"""
    try:
        scaler = joblib.load('../models/scaler_v1.pkl')
        survival = joblib.load('../models/weibull_aft_v1.pkl')
        classification = joblib.load('../models/xgboost_rca_v1.pkl')
        feature_names = joblib.load('../models/feature_names_v1.pkl')
        return {
            'scaler': scaler,
            'survival': survival,
            'classification': classification,
            'feature_names': feature_names
        }
    except:
        st.error("⚠️ Failed to load models. Run training first.")
        return None

@st.cache_data
def load_telemetry_data():
    """Load telemetry data"""
    try:
        return pd.read_parquet('../data/raw_telemetry.parquet')
    except:
        return None

@st.cache_data
def load_features_data():
    """Load engineered features"""
    try:
        return pd.read_parquet('../data/processed_features.parquet')
    except:
        return None

@st.cache_data
def load_labels_data():
    """Load failure labels"""
    try:
        return pd.read_parquet('../data/labels.parquet')
    except:
        return None

# ==================== MAIN DASHBOARD ====================

def main():
    
    # Header
    st.markdown("# 🏭 Industrial IoT Predictive Maintenance Dashboard")
    st.markdown("Real-time failure prediction and root cause analysis for switches, cameras, and sensors")
    
    # Load data
    models = load_models()
    telemetry = load_telemetry_data()
    features = load_features_data()
    labels = load_labels_data()
    
    if not all([models, telemetry, features, labels]):
        st.error("❌ Required data not found. Please run the full pipeline first:")
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
        ["📊 Dashboard", "⚠️ Alerts", "🔍 Device Details", "📈 Analytics", "ℹ️ Model Info"]
    )
    
    # Render selected page
    if page == "📊 Dashboard":
        show_dashboard(features, labels, models)
    elif page == "⚠️ Alerts":
        show_alerts(features, models)
    elif page == "🔍 Device Details":
        show_device_details(telemetry, features, labels, models)
    elif page == "📈 Analytics":
        show_analytics(labels, features)
    elif page == "ℹ️ Model Info":
        show_model_info(models, features)


def show_dashboard(features: pd.DataFrame, labels: pd.DataFrame, models: Dict):
    """Main dashboard view"""
    
    st.header("System Overview")
    
    # Key metrics
    col1, col2, col3, col4 = st.columns(4)
    
    total_devices = len(features)
    failed_devices = len(labels)
    failure_rate = (failed_devices / total_devices * 100) if total_devices > 0 else 0
    
    with col1:
        st.metric("Total Devices", total_devices, delta=None)
    with col2:
        st.metric("Failed Devices", failed_devices, delta=f"{failure_rate:.1f}%")
    with col3:
        avg_ttf = features['ttf_days'].mean() if 'ttf_days' in features else 0
        st.metric("Avg TTF (days)", f"{avg_ttf:.0f}")
    with col4:
        recent_failures = len(labels[
            labels['failure_timestamp'] > 
            datetime.now() - timedelta(days=30)
        ]) if not labels.empty else 0
        st.metric("Failures (30d)", recent_failures)
    
    # Risk distribution pie chart
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("Device Distribution by Type")
        device_counts = features['device_type'].value_counts()
        fig = px.pie(
            names=device_counts.index,
            values=device_counts.values,
            title="Devices by Type"
        )
        st.plotly_chart(fig, use_container_width=True)
    
    with col2:
        st.subheader("Failures by Mode")
        if not labels.empty:
            failure_modes = labels['failure_mode'].value_counts()
            fig = px.pie(
                names=failure_modes.index,
                values=failure_modes.values,
                title="Failure Distribution"
            )
            st.plotly_chart(fig, use_container_width=True)
    
    # Top risk factors
    st.subheader("Top Risk Factors (Global)")
    
    if 'device_age_days' in features:
        feature_correlations = {
            'temp_persistence': features['temp_persistence'].mean(),
            'voltage_volatility': features['voltage_volatility'].mean(),
            'packet_loss_trend': features['packet_loss_trend'].mean(),
            'memory_pressure': features['memory_pressure'].mean(),
            'device_age_days': features['device_age_days'].mean() / 365,  # Convert to years
        }
        
        risk_factors = pd.DataFrame(
            list(feature_correlations.items()),
            columns=['Factor', 'Average Value']
        ).sort_values('Average Value', ascending=False)
        
        fig = px.bar(
            risk_factors.head(10),
            x='Average Value',
            y='Factor',
            orientation='h',
            title="Key Risk Indicators"
        )
        st.plotly_chart(fig, use_container_width=True)
    
    # Failure timeline
    st.subheader("Failure Timeline (Last 90 Days)")
    
    if not labels.empty:
        labels['date'] = pd.to_datetime(labels['failure_timestamp']).dt.date
        failure_timeline = labels[
            labels['failure_timestamp'] > datetime.now() - timedelta(days=90)
        ].groupby('date').size().reset_index(name='count')
        
        if not failure_timeline.empty:
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=failure_timeline['date'],
                y=failure_timeline['count'],
                mode='lines+markers',
                fill='tozeroy',
                name='Failures'
            ))
            fig.update_layout(title="Daily Failures", xaxis_title="Date", yaxis_title="Count")
            st.plotly_chart(fig, use_container_width=True)


def show_alerts(features: pd.DataFrame, models: Dict):
    """Alert view"""
    
    st.header("⚠️ Critical & High-Risk Alerts")
    
    # Risk scoring (simplified)
    features['risk_score'] = (
        features['temp_persistence'] * 0.25 +
        features['voltage_volatility'] * 0.15 +
        features['packet_loss_mean_24h'] * 0.15 +
        features['memory_pressure'] * 0.20 +
        features['device_age_log'] * 0.15 +
        features['thermal_shock_events'] / 100 * 0.10
    )
    
    features['risk_level'] = pd.cut(
        features['risk_score'],
        bins=[0, 0.3, 0.5, 0.7, 1.0],
        labels=['LOW', 'MEDIUM', 'HIGH', 'CRITICAL']
    )
    
    # Filter alerts
    risk_filter = st.selectbox(
        "Filter by Risk Level",
        ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
    )
    
    alerts_filtered = features[features['risk_level'] == risk_filter].sort_values(
        'risk_score', ascending=False
    )
    
    if alerts_filtered.empty:
        st.info(f"✓ No {risk_filter} alerts")
        return
    
    st.subheader(f"{len(alerts_filtered)} {risk_filter} Alerts")
    
    # Display alerts
    for idx, (_, device) in enumerate(alerts_filtered.head(20).iterrows()):
        
        col1, col2, col3 = st.columns([1, 2, 1])
        
        with col1:
            risk_color = {
                'CRITICAL': '🔴',
                'HIGH': '🟠',
                'MEDIUM': '🟡',
                'LOW': '🟢'
            }
            st.markdown(f"### {risk_color[device['risk_level']]} {device['device_id']}")
            st.caption(f"{device['device_type']} • {device['zone']}")
        
        with col2:
            col2a, col2b = st.columns(2)
            with col2a:
                st.metric("Risk Score", f"{device['risk_score']:.2f}", delta=None)
            with col2b:
                st.metric("Days to Failure", f"{device.get('ttf_days', 'N/A')}")
            
            # Top factors
            factors = []
            if device['temp_persistence'] > 0.5:
                factors.append("🌡️ High thermal stress")
            if device['voltage_volatility'] > 1.5:
                factors.append("⚡ Power instability")
            if device['packet_loss_mean_24h'] > 1:
                factors.append("📡 Network degradation")
            if device['memory_pressure'] > 0.8:
                factors.append("💾 Memory pressure")
            if device['device_age_days'] > 1460:
                factors.append("📅 Device aging")
            
            if factors:
                st.write("**Contributing Factors:**")
                for factor in factors[:3]:
                    st.caption(f"• {factor}")
        
        with col3:
            st.button(
                "View Details",
                key=f"btn_{device['device_id']}",
                disabled=False
            )
        
        st.divider()


def show_device_details(telemetry: pd.DataFrame, features: pd.DataFrame, 
                       labels: pd.DataFrame, models: Dict):
    """Device details view"""
    
    st.header("🔍 Device Deep Dive")
    
    # Device selector
    device_id = st.selectbox(
        "Select Device",
        sorted(features['device_id'].unique()),
        key="device_selector"
    )
    
    if not device_id:
        return
    
    # Get device data
    device_features = features[features['device_id'] == device_id].iloc[0]
    device_telemetry = telemetry[telemetry['device_id'] == device_id]
    device_failures = labels[labels['device_id'] == device_id]
    
    # Device info
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Device Type", device_features['device_type'])
    with col2:
        st.metric("Zone", device_features['zone'])
    with col3:
        age_days = device_features['device_age_days']
        st.metric("Age", f"{age_days/365:.1f} years")
    
    st.divider()
    
    # Telemetry trends
    st.subheader("Recent Telemetry Trends")
    
    col1, col2 = st.columns(2)
    
    with col1:
        if 'temperature' in device_telemetry:
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=device_telemetry['timestamp'],
                y=device_telemetry['temperature'],
                mode='lines',
                name='Temperature',
                fill='tozeroy'
            ))
            fig.update_layout(
                title="Temperature (°C)",
                xaxis_title="Time",
                yaxis_title="Temperature"
            )
            st.plotly_chart(fig, use_container_width=True)
    
    with col2:
        if 'cpu_utilization' in device_telemetry:
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=device_telemetry['timestamp'],
                y=device_telemetry['cpu_utilization'],
                mode='lines',
                name='CPU',
                fill='tozeroy'
            ))
            fig.update_layout(
                title="CPU Utilization (%)",
                xaxis_title="Time",
                yaxis_title="CPU %"
            )
            st.plotly_chart(fig, use_container_width=True)
    
    # Risk factors
    st.subheader("Risk Factors")
    
    risk_factors = {
        'Thermal Stress': device_features.get('temp_persistence', 0),
        'Power Instability': device_features.get('voltage_volatility', 0),
        'Network Degradation': device_features.get('packet_loss_mean_24h', 0),
        'Memory Pressure': device_features.get('memory_pressure', 0),
        'Device Aging': device_features.get('device_age_log', 0) / 10,  # Scale
    }
    
    fig = px.bar(
        x=list(risk_factors.keys()),
        y=list(risk_factors.values()),
        labels={'y': 'Risk Score', 'x': 'Factor'}
    )
    fig.update_layout(title="Risk Factor Breakdown")
    st.plotly_chart(fig, use_container_width=True)
    
    # Failure history
    if not device_failures.empty:
        st.subheader("Failure History")
        st.dataframe(
            device_failures[['failure_timestamp', 'failure_mode']].head(5),
            use_container_width=True
        )


def show_analytics(labels: pd.DataFrame, features: pd.DataFrame):
    """Analytics view"""
    
    st.header("📈 System Analytics")
    
    if labels.empty:
        st.info("No failure data yet")
        return
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("Failures by Device Type")
        failure_by_type = labels.groupby('device_type').size()
        fig = px.bar(
            x=failure_by_type.index,
            y=failure_by_type.values,
            labels={'x': 'Device Type', 'y': 'Count'},
            title="Failure Count by Device Type"
        )
        st.plotly_chart(fig, use_container_width=True)
    
    with col2:
        st.subheader("Failures by Zone")
        failure_by_zone = labels.groupby('zone').size()
        fig = px.bar(
            x=failure_by_zone.index,
            y=failure_by_zone.values,
            labels={'x': 'Zone', 'y': 'Count'},
            title="Failure Count by Zone"
        )
        st.plotly_chart(fig, use_container_width=True)
    
    # Failure mode trends
    st.subheader("Failure Mode Distribution")
    if 'failure_mode' in labels:
        mode_counts = labels['failure_mode'].value_counts()
        fig = px.bar(
            x=mode_counts.index,
            y=mode_counts.values,
            labels={'x': 'Failure Mode', 'y': 'Count'}
        )
        st.plotly_chart(fig, use_container_width=True)


def show_model_info(models: Dict, features: pd.DataFrame):
    """Model information view"""
    
    st.header("ℹ️ Model Information")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("Models")
        st.write("""
        - **Stage 1**: Weibull AFT (Time-to-Failure)
        - **Stage 2**: XGBoost (Failure Mode Classification)
        - **Explainability**: SHAP
        """)
    
    with col2:
        st.subheader("Features")
        st.metric("Total Features", len(models['feature_names']))
        
    st.subheader("Feature List")
    feature_df = pd.DataFrame({
        'Feature': models['feature_names']
    })
    st.dataframe(feature_df, use_container_width=True)
    
    st.subheader("About")
    st.markdown("""
    This system predicts industrial IoT device failures using:
    
    - **Domain-specific features** tailored to switches, cameras, and sensors
    - **Survival analysis** to estimate time-to-failure
    - **Multi-class classification** to identify failure modes
    - **SHAP explanations** for model interpretability
    - **Real-time inference** via FastAPI
    - **Interactive monitoring** via Streamlit
    
    **Failure Modes:**
    - Hardware Burnout (thermal degradation)
    - Firmware Crash (software/memory issues)
    - Network Interface Failure (port degradation)
    - Power Supply Issues (voltage instability)
    - Environmental Factors (cooling, humidity, vibration)
    """)


if __name__ == "__main__":
    main()
