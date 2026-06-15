"""
Feature Engineering Pipeline for IoT Predictive Maintenance
Implements domain-specific features from the blueprint
"""

import pandas as pd
import numpy as np
from typing import Tuple, Dict, List
import logging

logger = logging.getLogger(__name__)


class FeatureEngineer:
    """Compute domain-specific features for industrial IoT assets"""
    
    def __init__(self, telemetry_df: pd.DataFrame):
        self.telemetry = telemetry_df.sort_values(['device_id', 'timestamp']).reset_index(drop=True)
        self.devices = self.telemetry['device_id'].unique()
        
    def compute_all_features(self) -> pd.DataFrame:
        """
        Compute all feature categories
        Returns: DataFrame with features, one row per device
        """
        features = {}
        
        logger.info("Computing features for all devices...")
        
        for device_id in self.devices:
            device_data = self.telemetry[self.telemetry['device_id'] == device_id]
            
            device_features = {
                'device_id': device_id,
                'device_type': device_data['device_type'].iloc[0],
                'zone': device_data['zone'].iloc[0],
                'install_date': device_data['install_date'].iloc[0],
            }
            
            # Feature categories
            device_features.update(self._compute_thermal_stress(device_data))
            device_features.update(self._compute_power_stability(device_data))
            device_features.update(self._compute_network_degradation(device_data))
            device_features.update(self._compute_memory_cpu_stress(device_data))
            device_features.update(self._compute_lifecycle_features(device_data))
            device_features.update(self._compute_environmental_resilience(device_data))
            device_features.update(self._compute_temporal_features(device_data))
            
            features[device_id] = device_features
        
        df = pd.DataFrame.from_dict(features, orient='index')
        
        logger.info(f"Generated {len(df.columns) - 4} features for {len(df)} devices")
        
        return df
    
    # ==================== THERMAL STRESS INDICATORS ====================
    def _compute_thermal_stress(self, device_data: pd.DataFrame) -> Dict:
        """
        Thermal indicators predict Hardware Burnout (correlation: 0.72)
        """
        temps = device_data['temperature'].values
        
        # Temperature_Delta_1H: Current - Mean(1H)
        temp_delta_1h = temps[-1] - np.mean(temps[-96:]) if len(temps) >= 96 else 0
        
        # Temperature_Persistence: % time > 75°C in 24H
        temp_persistence = np.mean(temps[-96:] > 75) if len(temps) >= 96 else 0
        
        # Temp_Over_Threshold_Duration: cumulative time above critical
        temp_over_threshold = np.sum(temps > 75)
        
        # Thermal_Shock_Events: count of |ΔTemp| > 20°C in 1H windows
        thermal_shocks = 0
        for i in range(96, len(temps)):
            if abs(temps[i] - temps[i-96]) > 20:
                thermal_shocks += 1
        
        return {
            'temp_delta_1h': temp_delta_1h,
            'temp_persistence': temp_persistence,
            'temp_over_threshold_duration': temp_over_threshold,
            'thermal_shock_events': thermal_shocks,
            'temp_max_24h': np.max(temps[-96:]) if len(temps) >= 96 else np.max(temps),
            'temp_mean_7d': np.mean(temps) if len(temps) > 0 else 0,
        }
    
    # ==================== POWER STABILITY INDICATORS ====================
    def _compute_power_stability(self, device_data: pd.DataFrame) -> Dict:
        """
        Power indicators predict Power Supply Degradation (correlation: 0.65)
        """
        voltages = device_data['poe_voltage'].values if 'poe_voltage' in device_data else np.array([48.0] * len(device_data))
        
        # Voltage_Volatility: StdDev(PoE_V, 1H)
        voltage_volatility = np.std(voltages[-96:]) if len(voltages) >= 96 else np.std(voltages)
        
        # Voltage_Sag_Events: count of V < (V_nominal - 5%) in 24H
        nominal_voltage = 48.0 if device_data['device_type'].iloc[0] in ["Switch", "Camera"] else 12.0
        voltage_sag_threshold = nominal_voltage * 0.95
        voltage_sag_events = np.sum(voltages[-96:] < voltage_sag_threshold) if len(voltages) >= 96 else 0
        
        # Power_Draw_Anomaly: Z-score
        if len(device_data) > 168:  # 7 days of data
            recent_draw = device_data['cpu_utilization'].iloc[-96:].mean()
            baseline_draw = device_data['cpu_utilization'].iloc[-168:].mean()
            draw_std = device_data['cpu_utilization'].iloc[-168:].std()
            power_draw_anomaly = (recent_draw - baseline_draw) / (draw_std + 1e-5)
        else:
            power_draw_anomaly = 0
        
        # PoE_Exhaustion_Index
        poe_exhaustion = (
            device_data['cpu_utilization'].iloc[-1] / 100 * 
            (device_data['ambient_temperature'].iloc[-1] / 20 if 'ambient_temperature' in device_data else 1)
        )
        
        return {
            'voltage_volatility': voltage_volatility,
            'voltage_sag_events': voltage_sag_events,
            'power_draw_anomaly': power_draw_anomaly,
            'poe_exhaustion_index': poe_exhaustion,
            'voltage_min_24h': np.min(voltages[-96:]) if len(voltages) >= 96 else np.min(voltages),
        }
    
    # ==================== NETWORK DEGRADATION ====================
    def _compute_network_degradation(self, device_data: pd.DataFrame) -> Dict:
        """
        Network indicators predict Port/Interface Failure (correlation: 0.68)
        """
        packet_loss = device_data['packet_loss'].values
        latency = device_data['ping_latency'].values if 'ping_latency' in device_data else np.array([5.0] * len(device_data))
        bandwidth = device_data['bandwidth_util'].values
        
        # Packet_Loss_Trend: slope over 7 days
        if len(packet_loss) >= 168:
            x = np.arange(len(packet_loss[-168:]))
            packet_loss_trend = np.polyfit(x, packet_loss[-168:], 1)[0]
        else:
            packet_loss_trend = 0
        
        # Latency_Variance: coefficient of variation (std / mean)
        if len(latency) >= 96 and np.mean(latency[-96:]) > 0:
            latency_variance = np.std(latency[-96:]) / np.mean(latency[-96:])
        else:
            latency_variance = 0
        
        # Bandwidth_Utilization_Spike
        if len(bandwidth) >= 168:
            bw_spike = np.max(bandwidth[-96:]) - np.mean(bandwidth[-168:])
        else:
            bw_spike = 0
        
        # Port_Error_Rate (proxy: high packet loss)
        port_error_rate = np.mean(packet_loss[-96:]) if len(packet_loss) >= 96 else np.mean(packet_loss)
        
        return {
            'packet_loss_trend': packet_loss_trend,
            'latency_variance': latency_variance,
            'bandwidth_spike': bw_spike,
            'port_error_rate': port_error_rate,
            'latency_mean_24h': np.mean(latency[-96:]) if len(latency) >= 96 else np.mean(latency),
            'packet_loss_mean_24h': np.mean(packet_loss[-96:]) if len(packet_loss) >= 96 else np.mean(packet_loss),
        }
    
    # ==================== MEMORY & CPU STRESS ====================
    def _compute_memory_cpu_stress(self, device_data: pd.DataFrame) -> Dict:
        """
        Memory/CPU indicators predict Firmware Crash (correlation: 0.58)
        """
        memory = device_data['memory_used'].values
        cpu = device_data['cpu_utilization'].values
        
        # Memory_Pressure
        memory_pressure = np.mean(memory[-96:]) / 100 if len(memory) >= 96 else np.mean(memory) / 100
        
        # Memory_Fragmentation_Proxy
        memory_fragmentation = (np.max(memory) - memory[-1]) if len(memory) > 0 else 0
        
        # CPU_Throttling_Events (high sustained CPU)
        cpu_throttling_events = np.sum(cpu[-96:] > 80) if len(cpu) >= 96 else 0
        
        # Swap_Usage_Rate (proxy: memory pressure high)
        swap_rate = np.mean(memory > 80) if len(memory) > 0 else 0
        
        return {
            'memory_pressure': memory_pressure,
            'memory_fragmentation': memory_fragmentation,
            'cpu_throttling_events': cpu_throttling_events,
            'swap_usage_rate': swap_rate,
            'cpu_mean_24h': np.mean(cpu[-96:]) if len(cpu) >= 96 else np.mean(cpu),
            'memory_max_24h': np.max(memory[-96:]) if len(memory) >= 96 else np.max(memory),
        }
    
    # ==================== LIFECYCLE & AGE ====================
    def _compute_lifecycle_features(self, device_data: pd.DataFrame) -> Dict:
        """
        Lifecycle features predict wear-out phase failures
        """
        from datetime import datetime
        
        install_date = device_data['install_date'].iloc[0]
        firmware_date = device_data['firmware_date'].iloc[0]
        current_date = device_data['timestamp'].max()
        
        # Device_Age_Days
        device_age_days = (current_date - install_date).days
        
        # Time_Since_Last_Failure (from labels)
        last_failure = device_data['failure_timestamp'].dropna()
        if len(last_failure) > 0:
            days_since_failure = (current_date - last_failure.iloc[-1]).days
        else:
            days_since_failure = -1  # No prior failure
        
        # Firmware_Age_Days
        firmware_age_days = (current_date - firmware_date).days if firmware_date else -1
        
        return {
            'device_age_days': device_age_days,
            'device_age_log': np.log1p(device_age_days),
            'device_age_squared': device_age_days ** 2,
            'days_since_last_failure': days_since_failure,
            'firmware_age_days': firmware_age_days,
        }
    
    # ==================== ENVIRONMENTAL RESILIENCE ====================
    def _compute_environmental_resilience(self, device_data: pd.DataFrame) -> Dict:
        """
        Environmental factors affecting device reliability
        """
        temps = device_data['temperature'].values if 'temperature' in device_data else np.array([45.0] * len(device_data))
        humidity = device_data['humidity'].values if 'humidity' in device_data else np.array([50.0] * len(device_data))
        vibration = device_data['vibration_score'].values if 'vibration_score' in device_data else np.array([2.0] * len(device_data))
        
        # Heat_Island_Index
        plant_avg_temp = 45
        zone_temp = np.mean(temps)
        heat_island = (zone_temp - plant_avg_temp) / 5  # Normalized by assumed std
        
        # Humidity_Stress (nonlinear: risk if < 30% or > 60%)
        humidity_stress = 0
        for h in humidity:
            if h < 30 or h > 60:
                humidity_stress += ((h - 45) ** 2)
        humidity_stress /= len(humidity)
        
        # Vibration_Cumulative_Energy
        vibration_energy = np.sum(vibration ** 2)
        
        # Environmental_Stability
        env_std = np.std(np.column_stack([temps, humidity, vibration]), axis=0).mean()
        
        return {
            'heat_island_index': heat_island,
            'humidity_stress': humidity_stress,
            'vibration_cumulative_energy': vibration_energy,
            'environmental_stability': env_std,
            'humidity_mean': np.mean(humidity),
            'vibration_mean': np.mean(vibration),
        }
    
    # ==================== TEMPORAL FEATURES ====================
    def _compute_temporal_features(self, device_data: pd.DataFrame) -> Dict:
        """
        Temporal patterns and seasonality
        """
        latest_timestamp = device_data['timestamp'].max()
        
        # Hour of day, day of week (sin/cos encoding)
        hour = latest_timestamp.hour
        day_of_week = latest_timestamp.weekday()
        month = latest_timestamp.month
        
        hour_sin = np.sin(2 * np.pi * hour / 24)
        hour_cos = np.cos(2 * np.pi * hour / 24)
        day_sin = np.sin(2 * np.pi * day_of_week / 7)
        day_cos = np.cos(2 * np.pi * day_of_week / 7)
        
        return {
            'hour_sin': hour_sin,
            'hour_cos': hour_cos,
            'day_sin': day_sin,
            'day_cos': day_cos,
            'month': month,
            'is_peak_hours': 1 if 10 <= hour <= 16 else 0,  # Peak load hours
        }


def create_labels_for_training(telemetry_df: pd.DataFrame, labels_df: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Create supervised learning labels from telemetry and failure logs
    
    Returns:
        y_time: Days to failure (or observation duration if censored)
        y_observed: 1 if failure, 0 if censored
        y_mode: Failure mode class
    """
    
    devices = telemetry_df['device_id'].unique()
    y_time = []
    y_observed = []
    y_mode = []
    
    for device_id in devices:
        device_data = telemetry_df[telemetry_df['device_id'] == device_id]
        last_obs = device_data['timestamp'].max()
        
        # Check if device has failure record
        device_failure = labels_df[labels_df['device_id'] == device_id]
        
        if len(device_failure) > 0:
            failure_time = device_failure['failure_timestamp'].iloc[-1]
            failure_mode = device_failure['failure_mode'].iloc[-1]
            
            # Time to event from last observation to failure
            ttf = (failure_time - last_obs).days
            
            # If ttf is negative, failure happened before observation window
            if ttf < -730:  # More than 2 years ago, skip
                continue
            
            y_time.append(max(ttf, 1))  # Minimum 1 day
            y_observed.append(1)
            y_mode.append(failure_mode)
        else:
            # Censored observation
            observation_duration = (last_obs - device_data['timestamp'].min()).days
            y_time.append(observation_duration)
            y_observed.append(0)
            y_mode.append('No_Failure')
    
    return np.array(y_time), np.array(y_observed), np.array(y_mode)


def main():
    """Compute features and save (or reuse existing)"""
    import os
    
    features_path = 'data/processed_features.parquet'
    
    # Check if features already exist
    if os.path.exists(features_path):
        logger.info("✓ Reusing existing features...")
        features_df = pd.read_parquet(features_path)
    else:
        # Load data
        telemetry_df = pd.read_parquet('data/raw_telemetry.parquet')
        labels_df = pd.read_parquet('data/labels.parquet')
        
        # Engineer features
        engineer = FeatureEngineer(telemetry_df)
        features_df = engineer.compute_all_features()
        
        # Merge with device metadata
        device_meta = telemetry_df.groupby('device_id').agg({
            'device_type': 'first',
            'zone': 'first',
            'install_date': 'first'
        }).reset_index()
        
        # Create labels
        y_time, y_observed, y_mode = create_labels_for_training(telemetry_df, labels_df)
        
        # Ensure length match
        if len(y_mode) == len(features_df):
            features_df['ttf_days'] = y_time
            features_df['failure_observed'] = y_observed
            features_df['failure_mode'] = y_mode
        
        # Save
        features_df.to_parquet(features_path, index=False)
    
    print(f"\n✓ Features computed: {len(features_df.columns) - 6} features")
    print(f"✓ Feature matrix shape: {features_df.shape}")
    print(f"\nFeature sample (first device):")
    print(features_df.iloc[0])


if __name__ == "__main__":
    main()
